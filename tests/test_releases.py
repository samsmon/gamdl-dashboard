import sys
import time

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.bus import EventBus
from app.config import Config
from app.releases import Itunes, ReleaseError, Watcher, display_title, kind_of

NOW = 1_000_000.0
ARTIST = {"wrapperType": "artist", "artistId": 111, "artistName": "Rokudenashi"}


def coll(cid, name, date, tracks=1, copyright="℗ 2026 Rokudenashi"):
    return {"wrapperType": "collection", "collectionId": cid, "collectionName": name, "artistId": 111, "artistName": "Rokudenashi",
            "releaseDate": date + "T08:00:00Z", "trackCount": tracks, "copyright": copyright}


class Fake:
    """Stands in for itunes.apple.com/lookup."""
    def __init__(self):
        self.albums = [coll(900, "Frozen Flower", "2025-12-24", 17), coll(901, "Living - Single", "2026-02-04")]
        self.fail = None
        self.calls = []

    async def __call__(self, params):
        self.calls.append(dict(params))
        if self.fail:
            raise self.fail
        if params.get("entity") == "song":
            a = next((x for x in self.albums if str(x["collectionId"]) == str(params["id"])), None)
            if not a:
                return {"results": []}
            songs = [{"wrapperType": "track", "kind": "song", "trackName": f"Song {i}", "trackNumber": i, "discNumber": 1, "trackTimeMillis": 1000 * i}
                     for i in range(1, (a["trackCount"] or 1) + 1)]
            return {"results": [a, *songs]}
        if params.get("entity") == "album":
            return {"results": [ARTIST, *self.albums]}
        ident = str(params["id"])
        if ident == "111":
            return {"results": [ARTIST]}
        for a in self.albums:
            if str(a["collectionId"]) == ident:
                return {"results": [a]}
        return {"results": []}


@pytest.fixture
def fake():
    return Fake()


@pytest.fixture
def watcher(store, fake):
    async def nosleep(s):
        return None
    return Watcher(store, EventBus(), client=Itunes(getter=fake), sleep=nosleep, clock=lambda: NOW)


def test_kind_of():
    assert kind_of("Living - Single") == "single" and kind_of("日陰 - EP") == "ep" and kind_of("Frozen Flower") == "album"


async def test_resolve_artist_url_album_url_and_id(fake):
    c = Itunes(getter=fake)
    assert await c.resolve("https://music.apple.com/jp/artist/rokudenashi/111", "jp") == ("111", "Rokudenashi")
    assert await c.resolve("https://music.apple.com/jp/album/frozen-flower/900?i=5", "jp") == ("111", "Rokudenashi")
    assert await c.resolve("901", "jp") == ("111", "Rokudenashi")
    assert await c.resolve("111", "jp") == ("111", "Rokudenashi")


async def test_resolve_rejects_garbage_and_unknown(fake):
    c = Itunes(getter=fake)
    for bad in ("", "nonsense", "https://example.com/artist/1"):
        with pytest.raises(ReleaseError):
            await c.resolve(bad, "jp")
    with pytest.raises(ReleaseError):
        await c.resolve("424242", "jp")


async def test_releases_parsed_and_artist_row_skipped(fake):
    rels = await Itunes(getter=fake).releases("111", "jp")
    assert [r["collection_id"] for r in rels] == ["900", "901"]
    assert rels[1] == {"collection_id": "901", "title": "Living - Single", "kind": "single", "release_date": "2026-02-04",
                       "track_count": 1, "copyright": "℗ 2026 Rokudenashi", "url": "https://music.apple.com/jp/album/901"}


async def test_first_check_is_baseline_then_only_new_releases_surface(store, watcher, fake):
    fid, created = store.add_follow("111", "Rokudenashi", "jp")
    assert created and store.add_follow("111", "x", "jp") == (fid, False)
    assert await watcher.check_one(fid) == 0
    assert store.count_releases("seen") == 2 and store.count_releases("new") == 0  # the back catalogue is not an inbox flood
    fake.albums.insert(0, coll(902, "Shooting Star - Single", "2026-07-15"))
    assert await watcher.check_one(fid) == 1
    assert [r["collection_id"] for r in store.list_releases("new")] == ["902"]
    assert await watcher.check_one(fid) == 0  # idempotent


async def test_label_filter_only_keeps_matching_copyright(store, watcher, fake):
    fake.albums.append(coll(903, "Other Label - Single", "2026-01-01", copyright="℗ 2026 Some Other Label"))
    fid, _ = store.add_follow("111", "Rokudenashi", "jp", "rokudenashi")
    await watcher.check_one(fid)
    assert sorted(r["collection_id"] for r in store.list_releases("seen")) == ["900", "901"]


async def test_api_failure_is_recorded_not_raised(store, watcher, fake):
    fid, _ = store.add_follow("111", "Rokudenashi", "jp")
    fake.fail = RuntimeError("boom")
    assert await watcher.check_one(fid) == 0
    f = store.get_follow(fid)
    assert "boom" in f["last_error"] and not f["baseline_done"]
    fake.fail = None
    await watcher.check_one(fid)
    f = store.get_follow(fid)
    assert f["last_error"] is None and f["baseline_done"] == 1


async def test_check_all_skips_recently_checked_unless_forced(store, watcher, fake):
    store.add_follow("111", "Rokudenashi", "jp")
    await watcher.check_all()
    n = len(fake.calls)
    await watcher.check_all()
    assert len(fake.calls) == n  # not due yet (release_check_hours)
    await watcher.check_all(force=True)
    assert len(fake.calls) == n + 1


def test_release_check_hours_setting_is_validated(store):
    assert store.get_settings()["release_check_hours"] == 6
    assert store.put_settings({"release_check_hours": 12})["release_check_hours"] == 12
    for bad in (0, 500, "x"):
        with pytest.raises(ValueError):
            store.put_settings({"release_check_hours": bad})


@pytest.fixture
def client(tmp_path, fake):
    cfg = Config(db_path=str(tmp_path / "d.sqlite"), gamdl_cmd=[sys.executable, "-c", "pass"], extra_args=[],
                 staging_dir=str(tmp_path / "st"), disk_path=str(tmp_path), cookies_path="", catalog_path="",
                 host="127.0.0.1", port=0, autostart=False, watch=False)
    app = create_app(cfg)
    app.state.watcher.client = Itunes(getter=fake)
    app.state.watcher._sleep = lambda s: __import__("asyncio").sleep(0)
    with TestClient(app) as c:
        c.fake = fake
        yield c


def wait_for(fn, seconds=3.0):
    end = time.time() + seconds
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.05)
    return fn()


def test_api_follow_baseline_new_release_and_add(client):
    r = client.post("/api/follows", json={"input": "https://music.apple.com/jp/album/frozen-flower/900"})
    assert r.status_code == 200 and r.json()["name"] == "Rokudenashi" and r.json()["created"]
    assert client.post("/api/follows", json={"input": "111"}).json()["created"] is False
    assert wait_for(lambda: client.get("/api/follows").json()[0]["last_checked"])
    assert client.get("/api/releases?status=new").json() == []
    assert len(client.get("/api/releases?status=seen").json()) == 2
    client.fake.albums.insert(0, coll(902, "Shooting Star - Single", "2026-07-15"))
    client.post("/api/follows/check")
    new = wait_for(lambda: client.get("/api/releases?status=new").json())
    assert [x["collection_id"] for x in new] == ["902"] and new[0]["artist"] == "Rokudenashi" and new[0]["queued"] is False
    assert client.get("/api/state").json()["releases"] == {"new": 1, "follows": 1}
    res = client.post("/api/releases/bulk", json={"ids": [new[0]["id"]], "action": "add"}).json()["results"][0]
    assert res["item_id"] and res["error"] is None
    item = client.get(f"/api/queue/{res['item_id']}").json()
    assert item["url"] == "https://music.apple.com/jp/album/902" and item["title"] == "Shooting Star - Single" and item["artist"] == "Rokudenashi"
    assert client.get("/api/releases?status=new").json() == [] and len(client.get("/api/releases?status=added").json()) == 1
    again = client.post("/api/releases/bulk", json={"ids": [new[0]["id"]], "action": "add"}).json()["results"][0]
    assert again["item_id"] is None and "already" in again["error"]  # never queued twice


def test_api_dismiss_restore_unfollow_and_validation(client):
    client.post("/api/follows", json={"input": "111", "label_filter": "rokudenashi"})
    wait_for(lambda: client.get("/api/follows").json()[0]["last_checked"])
    seen = client.get("/api/releases?status=seen").json()
    ids = [x["id"] for x in seen]
    client.post("/api/releases/bulk", json={"ids": ids, "action": "dismiss"})
    assert len(client.get("/api/releases?status=dismissed").json()) == 2
    client.post("/api/releases/bulk", json={"ids": ids, "action": "restore"})
    assert len(client.get("/api/releases?status=new").json()) == 2
    fid = client.get("/api/follows").json()[0]["id"]
    assert client.put(f"/api/follows/{fid}", json={"label_filter": ""}).json()["label_filter"] is None
    assert client.post("/api/releases/bulk", json={"ids": ids, "action": "bogus"}).status_code == 422
    assert client.get("/api/releases?status=bogus").status_code == 422
    assert client.post("/api/follows", json={"input": "not an id"}).status_code == 422
    assert client.put("/api/follows/999", json={"label_filter": "x"}).status_code == 404
    client.delete(f"/api/follows/{fid}")
    assert client.get("/api/follows").json() == [] and client.get("/api/releases?status=all").json() == []


def test_api_follow_reports_apple_failure_as_502(client):
    client.fake.fail = RuntimeError("down")
    assert client.post("/api/follows", json={"input": "111"}).status_code == 502


def test_display_title_always_carries_the_kind():
    assert display_title("Living - Single", "single") == "Living - Single"
    assert display_title("\u65e5\u9670 - EP", "ep") == "\u65e5\u9670 - EP"
    assert display_title("Frozen Flower", "album") == "Frozen Flower - Album"


async def test_album_metadata_includes_the_full_track_list(fake):
    meta = await Itunes(getter=fake).album("901", "jp")
    assert meta == {"name": "Living - Single", "kind": "single", "artist": "Rokudenashi", "track_count": 1,
                    "release_date": "2026-02-04", "genre": "", "copyright": "\u2117 2026 Rokudenashi", "explicit": False, "artwork": "", "url": "",
                    "total_ms": 1000, "tracks": [{"n": 1, "title": "Song 1", "ms": 1000, "artist": "", "explicit": False}]}
    assert len((await Itunes(getter=fake).album("900", "jp"))["tracks"]) == 17
    assert await Itunes(getter=fake).album("424242", "jp") is None


async def test_album_metadata_artwork_genre_and_explicit(fake):
    a = coll(960, "Loud - EP", "2026-03-03", 2)
    a.update(primaryGenreName="J-Pop", collectionExplicitness="explicit", artworkUrl100="https://is1-ssl.mzstatic.com/image/thumb/x/100x100bb.jpg",
             collectionViewUrl="https://music.apple.com/jp/album/960")
    fake.albums.append(a)
    m = await Itunes(getter=fake).album("960", "jp")
    assert m["genre"] == "J-Pop" and m["explicit"] and m["kind"] == "ep" and m["total_ms"] == 3000
    assert m["artwork"] == "https://is1-ssl.mzstatic.com/image/thumb/x/300x300bb.jpg"
    a["artworkUrl100"] = "javascript:alert(1)"
    assert (await Itunes(getter=fake).album("960", "jp"))["artwork"] == ""  # only https artwork is ever handed to the UI
