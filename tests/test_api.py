import csv
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.config import Config

FAKE = str(Path(__file__).resolve().parent.parent / "tools" / "fake_gamdl_safe.py")
CANARY = "CANARY-SECRET-COOKIE-VALUE"


@pytest.fixture
def client(tmp_path):
    cookies = tmp_path / "cookies.txt"
    cookies.write_text(f".apple.com\tTRUE\t/\tTRUE\t4102444800\tsession\t{CANARY}\n")
    meta = tmp_path / "metadata.csv"  # a one-album library: 溜息 by ロクデナシ, lossless
    with open(meta, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Title", "Artist", "Album", "Album Artist", "Codec", "Duration", "Path"])
        for t, d in (("心の奥", 200.0), ("溜息", 240.0)):
            w.writerow([t, "ロクデナシ", "溜息", "ロクデナシ", "audio/flac", d, f"E:/Music\\L\\溜息\\{t}.flac"])
    cfg = Config(db_path=str(tmp_path / "d.sqlite"), gamdl_cmd=[sys.executable, FAKE], extra_args=[],
                 staging_dir=str(tmp_path / "st"), disk_path=str(tmp_path), cookies_path=str(cookies),
                 catalog_path=str(tmp_path / "none.sqlite"), host="127.0.0.1", port=0, autostart=False,
                 library_csv=str(meta))
    app = create_app(cfg)
    with TestClient(app) as c:
        c.cfg = cfg
        yield c


def q(client, *urls):
    return client.post("/api/queue", json={"items": [{"url": u} for u in urls]}).json()


def test_parse_reports_jp_normalization_and_errors(client):
    r = client.post("/api/parse", json={"text": "https://music.apple.com/id/album/frozen-flower/1851922484 nonsense"}).json()
    assert r["results"][0]["url"] == "https://music.apple.com/jp/album/1851922484"
    assert r["results"][1]["error"]


def test_queue_multiple_dedupes_and_normalizes(client):
    r = q(client, "https://music.apple.com/jp/album/%E6%BA%9C%E6%81%AF/1791035368",
          "https://music.apple.com/id/album/frozen-flower/1851922484",
          "https://music.apple.com/us/album/again/1791035368", "https://evil.example/x")
    assert [bool(x["id"]) for x in r["results"]] == [True, True, False, False]
    assert r["results"][2]["error"] == "already in queue or history"
    st = client.get("/api/state").json()
    assert [i["url"] for i in st["items"]] == [
        "https://music.apple.com/jp/album/1791035368", "https://music.apple.com/jp/album/1851922484"]


def test_controls_reorder_retry_remove(client):
    ids = [x["id"] for x in q(client, "https://music.apple.com/jp/album/1", "https://music.apple.com/jp/album/2")["results"]]
    client.post("/api/queue/reorder", json={"ids": ids[::-1]})
    assert [i["id"] for i in client.get("/api/state").json()["items"]] == ids[::-1]
    assert client.post(f"/api/queue/{ids[0]}/cancel").json() == {"ok": True}
    assert client.get(f"/api/queue/{ids[0]}").json()["status"] == "cancelled"
    assert client.post(f"/api/queue/{ids[0]}/retry").json() == {"ok": True}
    assert client.get(f"/api/queue/{ids[0]}").json()["status"] == "queued"
    assert client.post(f"/api/queue/{ids[1]}/remove").json() == {"ok": True}
    assert client.post("/api/queue/999/cancel").status_code == 404


def test_retry_original_uses_original_storefront(client):
    r = q(client, "https://music.apple.com/id/album/x/77")["results"][0]
    client.post(f"/api/queue/{r['id']}/cancel")
    assert client.post(f"/api/queue/{r['id']}/retry_original").json() == {"ok": True}
    it = client.get(f"/api/queue/{r['id']}").json()
    assert it["url"] == "https://music.apple.com/id/album/77" and it["status"] == "queued"


def test_settings_roundtrip_and_validation(client):
    assert client.get("/api/settings").json()["storefront"] == "jp"
    assert client.put("/api/settings", json={"error_threshold": 4}).json()["error_threshold"] == 4
    bad = client.put("/api/settings", json={"track_delay": "1-2"})
    assert bad.status_code == 422 and "track_delay" in bad.json()["detail"]


def test_pause_resume_flags(client):
    client.post("/api/pause")
    assert client.get("/api/state").json()["paused"] is True
    client.post("/api/resume")
    assert client.get("/api/state").json()["paused"] is False


def test_preview_survives_missing_catalog_and_network(client, monkeypatch):
    import app.api as api_mod

    async def boom(url): raise RuntimeError("offline")
    monkeypatch.setattr(api_mod, "PREVIEW_GETTER", boom, raising=False)
    r = client.post("/api/preview", json={"url": "https://music.apple.com/jp/album/1"}).json()
    assert r["preview"]["source"] == "none"
    assert r["library"]["status"] in ("unknown", "unavailable", "new")
    assert r["library"]["needs_force"] is False and r["library"]["default_checked"] is True


def album_page():
    ld = {"@type": "MusicAlbum", "name": "溜息", "byArtist": {"name": "ロクデナシ"}, "numTracks": 2,
          "track": [{"name": "心の奥", "duration": "PT3M20S"}, {"name": "溜息", "duration": "PT4M0S"}]}
    return f'<script type="application/ld+json">{json.dumps(ld, ensure_ascii=False)}</script>'


def test_preview_flags_lossless_library_hit_and_queue_needs_force(client, monkeypatch):
    import app.api as api_mod

    async def getter(url):
        return album_page()

    monkeypatch.setattr(api_mod, "PREVIEW_GETTER", getter, raising=False)
    url = "https://music.apple.com/jp/album/42"
    r = client.post("/api/preview", json={"url": url}).json()
    assert r["library"]["status"] == "in_library_lossless" and r["library"]["confidence"] >= 0.85
    assert r["library"]["needs_force"] is True and r["library"]["default_checked"] is False
    assert r["library"]["source"] == "metadata"
    refused = client.post("/api/queue", json={"items": [{"url": url}]}).json()["results"][0]
    assert refused["id"] is None and "download anyway" in refused["error"]
    ok = client.post("/api/queue", json={"items": [{"url": url, "force": True}]}).json()["results"][0]
    assert ok["id"] and client.get(f"/api/queue/{ok['id']}").json()["library_status"] == "in_library_lossless"


def test_unpreviewed_url_can_be_queued_without_force(client):
    r = client.post("/api/queue", json={"items": [{"url": "https://music.apple.com/jp/album/43"}]}).json()
    assert r["results"][0]["id"]


def test_new_album_is_default_checked(client, monkeypatch):
    import app.api as api_mod

    async def getter(url):
        return album_page().replace("溜息", "Different Album").replace("心の奥", "Other Song")

    monkeypatch.setattr(api_mod, "PREVIEW_GETTER", getter, raising=False)
    r = client.post("/api/preview", json={"url": "https://music.apple.com/jp/album/44"}).json()
    assert r["library"]["status"] == "new" and r["library"]["default_checked"] is True


def test_cookie_values_never_leak(client, tmp_path):
    q(client, "https://music.apple.com/jp/album/1")
    bodies = [client.get(p).text for p in ("/api/state", "/api/settings", "/api/history", "/api/log")]
    assert all(CANARY not in b for b in bodies)
    assert "expiry_days" in bodies[0]
    assert CANARY.encode() not in Path(client.cfg.db_path).read_bytes()


def test_static_index_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "gamdl" in r.text.lower()
