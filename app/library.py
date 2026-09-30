import csv
import difflib
import os
import re
import unicodedata
from dataclasses import dataclass, field

from app.catalog import artist_variants

LOSSLESS = ("flac", "alac", "wavpack", "wave", "wav", "pcm", "aiff", "ape", "tta")
_TAGS = re.compile(r"\[[^\]]*\]")
_SUFFIX = re.compile(r"\s*-\s*(single|ep)\s*$", re.I)
W_TITLE, W_DURATION, W_OVERLAP, W_COUNT, ARTIST_BONUS = 0.35, 0.25, 0.25, 0.15, 0.05
THIN_EVIDENCE_CAP = 0.80
TITLE_ONLY_MIN = 0.90  # with only a title to go on (Apple page gave no tracks), below this and no artist match it is discounted


def norm_title(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").casefold()
    s = _TAGS.sub(" ", s)
    s = _SUFFIX.sub("", s)
    s = re.sub(r"[^\w]+", " ", s)
    return " ".join(s.split())


def is_lossless_codec(codec: str) -> bool:
    c = (codec or "").lower()
    return any(t in c for t in LOSSLESS)


@dataclass
class RemoteAlbum:
    title: str
    artist: str
    tracks: int | None = None
    track_titles: list = field(default_factory=list)
    durations: list = field(default_factory=list)


@dataclass
class LibraryMatch:
    status: str
    confidence: float = 0.0
    album: str = ""
    path: str = ""
    lossless: bool | None = None
    reasons: list = field(default_factory=list)


@dataclass
class _Rec:
    dir: str
    title: str
    artists: set
    tracks: set
    count: int
    duration: float
    lossless: bool


def _float(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


class Library:
    def __init__(self, path: str):
        self.path = path
        self._mtime = None
        self._recs: list = []

    def mtime(self):
        try:
            return os.stat(self.path).st_mtime
        except OSError:
            return None

    def _load(self):
        m = os.stat(self.path).st_mtime
        if m == self._mtime:
            return
        groups: dict = {}
        with open(self.path, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                p = (row.get("Path") or "").replace("/", "\\")
                d = p.rsplit("\\", 1)[0] if "\\" in p else f"?{row.get('Album')}"
                g = groups.setdefault(d, {"title": row.get("Album") or "", "artists": set(), "tracks": set(),
                                          "n": 0, "dur": 0.0, "lossless": True})
                g["artists"] |= artist_variants(row.get("Album Artist") or "") | artist_variants(row.get("Artist") or "")
                g["tracks"].add(norm_title(row.get("Title") or ""))
                g["n"] += 1
                g["dur"] += _float(row.get("Duration"))
                g["lossless"] = g["lossless"] and is_lossless_codec(row.get("Codec") or "")
        self._recs = [_Rec(d, norm_title(g["title"]), g["artists"], g["tracks"] - {""}, g["n"], g["dur"], g["lossless"])
                      for d, g in groups.items()]
        self._mtime = m

    @staticmethod
    def _score(rec: _Rec, r: RemoteAlbum, title: str, titles: set) -> float:
        comps = []
        if title and rec.title:
            if title == rec.title:
                ratio = 1.0
            else:
                sm = difflib.SequenceMatcher(None, title, rec.title)
                ratio = sm.ratio() if sm.quick_ratio() >= 0.5 else 0.0
            comps.append((W_TITLE, ratio))
        if r.tracks:
            comps.append((W_COUNT, 1 - abs(r.tracks - rec.count) / max(r.tracks, rec.count)))
        if r.durations and all(d is not None for d in r.durations) and rec.duration > 0:
            total = sum(r.durations)
            diff = abs(total - rec.duration)
            comps.append((W_DURATION, 1.0 if diff <= 3 else max(0.0, 1 - (diff - 3) / (0.05 * max(total, rec.duration)))))
        if titles and rec.tracks:
            comps.append((W_OVERLAP, len(titles & rec.tracks) / max(len(titles), len(rec.tracks))))
        if not comps:
            return 0.0
        score = sum(w * v for w, v in comps) / sum(w for w, _ in comps)
        artist_hit = bool(r.artist and (artist_variants(r.artist) & rec.artists))
        if len(comps) == 1 and not artist_hit and score < TITLE_ONLY_MIN:
            score *= 0.5  # a bare fuzzy title ("diamonds" ~ "star diamond") is not evidence of the same album
        if artist_hit:
            score = min(1.0, score + ARTIST_BONUS)
        if len(comps) < 3:
            score = min(score, THIN_EVIDENCE_CAP)
        return score

    def match(self, r: RemoteAlbum, exact: float = 0.85, similar: float = 0.55) -> LibraryMatch:
        try:
            self._load()
        except (OSError, csv.Error, UnicodeDecodeError) as e:
            return LibraryMatch("unavailable", reasons=[str(e)])
        title = norm_title(r.title)
        titles = {norm_title(t) for t in r.track_titles} - {""}
        if not title and not titles:
            return LibraryMatch("unknown", reasons=["no album data from the Apple page"])
        scored = sorted(((self._score(rec, r, title, titles), rec) for rec in self._recs), key=lambda x: -x[0])
        if not scored:
            return LibraryMatch("new")
        best, rec = scored[0]
        near = [x for s, x in scored if s >= best - 0.05]
        chosen = next((x for x in near if x.lossless), rec)
        if best >= exact:
            status = "in_library_lossless" if chosen.lossless else "in_library_lossy"
        elif best >= similar:
            status = "similar"
        else:
            return LibraryMatch("new", round(best, 3))
        return LibraryMatch(status, round(best, 3), chosen.title, chosen.dir, chosen.lossless)
