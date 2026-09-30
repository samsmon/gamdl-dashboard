import re
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlsplit

KINDS = {"album", "playlist", "song", "artist"}
HOSTS = {"music.apple.com", "classical.music.apple.com"}


class UrlError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedUrl:
    kind: str
    id: str
    track_id: str | None
    storefront: str
    original: str
    normalized: str

    @property
    def key(self) -> tuple:
        return (self.kind, self.id, self.track_id)


@dataclass
class UrlResult:
    raw: str
    parsed: ParsedUrl | None
    error: str | None


def normalize(raw: str, storefront: str | None = "jp") -> ParsedUrl:
    raw = raw.strip()
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https"):
        raise UrlError("not an http(s) url")
    if (parts.hostname or "").lower() not in HOSTS:
        raise UrlError("not an Apple Music url")
    segs = [unquote(s) for s in parts.path.split("/") if s]
    if len(segs) < 3:
        raise UrlError("url has no item id")
    sf, kind, ext_id = segs[0].lower(), segs[1].lower(), segs[-1]
    if not re.fullmatch(r"[a-z]{2}", sf):
        raise UrlError("missing storefront")
    if kind not in KINDS:
        raise UrlError(f"unsupported type: {kind}")
    ok = ext_id.startswith("pl.") if kind == "playlist" else ext_id.isdigit()
    if not ok or not re.fullmatch(r"[A-Za-z0-9._\-]+", ext_id):
        raise UrlError("bad item id")
    track_id = (parse_qs(parts.query).get("i") or [None])[0]
    if track_id is not None and not track_id.isdigit():
        track_id = None
    target = (storefront or sf).lower()
    normalized = f"https://music.apple.com/{target}/{kind}/{ext_id}"
    if track_id:
        normalized += f"?i={track_id}"
    return ParsedUrl(kind, ext_id, track_id, target, raw, normalized)


def parse_many(text: str, storefront: str | None = "jp") -> list:
    results, seen = [], set()
    for raw in (t for t in re.split(r"[\s,]+", text) if t):
        try:
            p = normalize(raw, storefront)
        except UrlError as e:
            results.append(UrlResult(raw, None, str(e)))
            continue
        if p.key in seen:
            results.append(UrlResult(raw, None, "duplicate"))
            continue
        seen.add(p.key)
        results.append(UrlResult(raw, p, None))
    return results
