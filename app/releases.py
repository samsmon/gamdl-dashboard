"""Follow artists and surface their new releases.

Data comes from Apple's public iTunes lookup API (no account, no cookies, so it never touches the download account):
  https://itunes.apple.com/lookup?id=<artistId>&entity=album&country=jp&sort=recent
The watcher polls followed artists slowly and sequentially; new releases land in an inbox, the user decides what to download.
"""
import asyncio
import re
import time

import httpx

API = "https://itunes.apple.com/lookup"
_ARTIST_URL = re.compile(r"music\.apple\.com/(?:[a-z]{2}/)?artist/(?:[^/?#]+/)?(\d+)", re.I)
_ALBUM_URL = re.compile(r"music\.apple\.com/(?:[a-z]{2}/)?album/(?:[^/?#]+/)?(\d+)", re.I)
GAP_SECONDS = 3.0  # between two artists in one sweep, well under the API's ~20 requests/minute


class ReleaseError(ValueError):
    pass


def kind_of(name: str) -> str:
    n = (name or "").strip().lower()
    if n.endswith("- single"):
        return "single"
    if n.endswith("- ep"):
        return "ep"
    return "album"


def display_title(name: str, kind: str) -> str:
    """"Living - Single" / "日陰 - EP" / "Frozen Flower - Album": always name plus its kind."""
    base = re.sub(r"\s*-\s*(single|ep)\s*$", "", name or "", flags=re.I).strip()
    return f"{base} - {'EP' if kind == 'ep' else kind.capitalize()}"


def _date(s) -> str:
    return (s or "")[:10]


class Itunes:
    def __init__(self, getter=None):
        self._getter = getter or self._http

    @staticmethod
    async def _http(params: dict) -> dict:
        async with httpx.AsyncClient(headers={"User-Agent": "gamdl-dashboard"}, timeout=20) as c:
            r = await c.get(API, params=params)
            r.raise_for_status()
            return r.json()

    async def resolve(self, text: str, storefront: str) -> tuple:
        """Artist url, album url or bare id -> (artist_id, artist_name). An album resolves to its artist."""
        text = (text or "").strip()
        m = _ARTIST_URL.search(text) or _ALBUM_URL.search(text)
        ident = m.group(1) if m else text if text.isdigit() else None
        if not ident:
            raise ReleaseError("expected an Apple Music artist url, album url or numeric id")
        data = await self._getter({"id": ident, "country": storefront})
        rows = data.get("results") or []
        if not rows:
            raise ReleaseError(f"nothing found for id {ident} in storefront {storefront}")
        r = rows[0]
        artist_id, name = r.get("artistId"), r.get("artistName")
        if not artist_id or not name:
            raise ReleaseError("could not work out the artist for that id")
        return str(artist_id), name

    async def album(self, collection_id: str, storefront: str):
        """Name, kind, artist and the full track list of one album, or None when Apple does not know it."""
        data = await self._getter({"id": collection_id, "entity": "song", "country": storefront, "limit": 200})
        rows = data.get("results") or []
        coll = next((r for r in rows if r.get("wrapperType") == "collection"), None)
        if not coll:
            return None
        songs = sorted((r for r in rows if r.get("wrapperType") == "track" and r.get("kind") == "song"),
                       key=lambda r: (r.get("discNumber") or 1, r.get("trackNumber") or 0))
        name = coll.get("collectionName") or ""
        art = (coll.get("artworkUrl100") or "").replace("100x100bb", "300x300bb")
        tracks = [{"n": i, "title": s.get("trackName") or "", "ms": s.get("trackTimeMillis"), "artist": s.get("artistName") or "",
                   "explicit": s.get("trackExplicitness") == "explicit"} for i, s in enumerate(songs, 1)]
        return {"name": name, "kind": kind_of(name), "artist": coll.get("artistName") or "",
                "track_count": coll.get("trackCount") or len(songs),
                "release_date": _date(coll.get("releaseDate")), "genre": coll.get("primaryGenreName") or "",
                "copyright": coll.get("copyright") or "", "explicit": coll.get("collectionExplicitness") == "explicit",
                "artwork": art if art.startswith("https://") else "", "url": coll.get("collectionViewUrl") or "",
                "total_ms": sum(t["ms"] or 0 for t in tracks), "tracks": tracks}

    async def releases(self, artist_id: str, storefront: str) -> list:
        data = await self._getter({"id": artist_id, "entity": "album", "country": storefront, "limit": 200, "sort": "recent"})
        out = []
        for r in data.get("results") or []:
            if r.get("wrapperType") != "collection" or not r.get("collectionId"):
                continue
            cid = str(r["collectionId"])
            out.append({"collection_id": cid, "title": r.get("collectionName") or "", "kind": kind_of(r.get("collectionName")),
                        "release_date": _date(r.get("releaseDate")), "track_count": r.get("trackCount"),
                        "copyright": r.get("copyright") or "", "url": f"https://music.apple.com/{storefront}/album/{cid}"})
        return out


class Watcher:
    def __init__(self, store, bus, client=None, sleep=asyncio.sleep, clock=time.time, gap=GAP_SECONDS):
        self.store, self.bus, self.client = store, bus, client or Itunes()
        self._sleep, self._clock, self._gap = sleep, clock, gap
        self._wake = asyncio.Event()
        self._force = False
        self._stopping = False
        self._lock = asyncio.Lock()

    def wake(self, force: bool = False):
        self._force = self._force or force
        self._wake.set()

    def stop(self):
        self._stopping = True
        self._wake.set()

    async def check_follow(self, f: dict) -> int:
        """Fetch one artist. The first successful check of a follow only records the back catalogue as 'seen'."""
        now = self._clock()
        try:
            rels = await self.client.releases(f["artist_id"], f["storefront"])
        except Exception as e:  # network/API trouble must never kill the loop; it is shown on the follow instead
            self.store.update_follow(f["id"], last_checked=now, last_error=f"{type(e).__name__}: {e}"[:200])
            return 0
        first = not f["baseline_done"]
        filt = (f["label_filter"] or "").strip().lower()
        new = 0
        for r in rels:
            if filt and filt not in r["copyright"].lower():
                continue
            if self.store.add_release(f["id"], r, "seen" if first else "new") and not first:
                new += 1
        self.store.update_follow(f["id"], last_checked=now, last_error=None, baseline_done=1)
        return new

    async def check_one(self, follow_id: int) -> int:
        async with self._lock:
            f = self.store.get_follow(follow_id)
            n = await self.check_follow(f) if f else 0
            self.bus.publish({"type": "releases"})
            return n

    async def check_all(self, force: bool = False) -> int:
        async with self._lock:
            hours = self.store.get_settings()["release_check_hours"]
            total, first = 0, True
            for f in self.store.list_follows():
                if self._stopping:
                    break
                if not force and f["last_checked"] and self._clock() - f["last_checked"] < hours * 3600:
                    continue
                if not first:
                    await self._sleep(self._gap)
                first = False
                total += await self.check_follow(f)
            self.bus.publish({"type": "releases"})
            return total

    async def run_forever(self):
        while not self._stopping:
            force, self._force = self._force, False
            try:
                await self.check_all(force)
            except Exception:  # keep the watcher alive whatever a single sweep does
                pass
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=600)
            except asyncio.TimeoutError:
                pass
