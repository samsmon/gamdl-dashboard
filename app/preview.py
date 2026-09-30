import asyncio
import html as htmllib
import json
import random
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from app.library import RemoteAlbum

_ISO = re.compile(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?$")
_LD = re.compile(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S | re.I)
_OG = re.compile(r'<meta[^>]+property="og:title"[^>]+content="([^"]*)"', re.I)
_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"


@dataclass
class Preview:
    title: str
    artist: str
    tracks: int | None
    year: str | None
    source: str
    track_titles: list = field(default_factory=list)
    durations: list = field(default_factory=list)

    def remote(self) -> RemoteAlbum:
        return RemoteAlbum(self.title, self.artist, self.tracks, list(self.track_titles), list(self.durations))


def parse_iso_duration(s):
    if not isinstance(s, str):
        return None
    m = _ISO.match(s)
    if not m or not any(m.groups()):
        return None
    h, mi, sec = m.groups()
    return int(h or 0) * 3600 + int(mi or 0) * 60 + float(sec or 0)


def _artist(by) -> str:
    if isinstance(by, list):
        by = by[0] if by else {}
    if isinstance(by, dict):
        return _text(by.get("name"))
    return _text(by)


def _text(v) -> str:
    return v if isinstance(v, str) else ""


def _count(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, str) and v.strip().isdigit():
        v = int(v.strip())
    return v if isinstance(v, int) and v > 0 else None


def parse_page(page: str):
    for raw in _LD.findall(page):
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        for d in data if isinstance(data, list) else [data]:
            if isinstance(d, dict) and d.get("@type") in ("MusicAlbum", "MusicPlaylist"):
                listed = [t for t in d.get("track", []) if isinstance(t, dict)] if isinstance(d.get("track"), list) else []
                tracks = _count(d.get("numTracks"))
                if tracks is None and listed:
                    tracks = len(listed)
                year = str(d.get("datePublished", ""))[:4] or None
                return Preview(_text(d.get("name")), _artist(d.get("byArtist")), tracks, year, "web",
                               [_text(t.get("name")) for t in listed], [parse_iso_duration(t.get("duration")) for t in listed])
    m = _OG.search(page)
    if m:
        title = htmllib.unescape(m[1]).removesuffix(" on Apple Music").strip()
        return Preview(title, "", None, None, "web")
    return None


def _host_allowed(url: str) -> bool:
    host = (urlsplit(str(url)).hostname or "").lower()
    return host == "apple.com" or host.endswith(".apple.com")


async def _default_getter(url: str) -> str:
    import httpx
    headers = {"User-Agent": _UA, "Accept-Language": "ja-JP,ja;q=0.9"}
    async with httpx.AsyncClient(timeout=15, follow_redirects=True, max_redirects=3, headers=headers) as c:
        r = await c.get(url)
        r.raise_for_status()
        if not all(_host_allowed(str(x.url)) for x in [*r.history, r]):
            raise ValueError("redirected outside apple.com")
        return r.text


class PreviewService:
    def __init__(self, getter=None, delay=(3.0, 8.0), sleep=asyncio.sleep, rand=random.uniform):
        self._get = getter or _default_getter
        self._delay, self._sleep, self._rand = delay, sleep, rand
        self._lock = asyncio.Lock()
        self._first = True

    async def fetch(self, parsed) -> Preview:
        async with self._lock:
            if not self._first:
                await self._sleep(self._rand(*self._delay))
            self._first = False
            try:
                page = await self._get(parsed.normalized)
                return parse_page(page) or Preview("", "", None, None, "none")
            except Exception:
                return Preview("", "", None, None, "none")
