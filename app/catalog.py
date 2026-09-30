import difflib
import os
import re
import sqlite3
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

_SUFFIX = re.compile(r"\s*-\s*(single|ep)\s*$", re.I)


@dataclass
class Match:
    level: str
    paths: list = field(default_factory=list)
    tracks: int = 0
    lossless: bool = False
    note: str = ""


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").casefold()
    s = _SUFFIX.sub("", s)
    s = re.sub(r"[^\w]+", " ", s)
    return " ".join(s.split())


def artist_variants(s: str) -> set:
    s = unicodedata.normalize("NFKC", s or "").replace("~", " ")
    parts = [p for p in re.split(r"[()]", s) if p.strip()]
    return {norm(p) for p in parts + [s]} - {""}


def _rank(entries, artist, album):
    a, av = norm(album), artist_variants(artist)
    if not a:
        return [], []
    exact, likely = [], []
    for alb, ents in entries:
        if alb == a:
            ratio = 1.0
        elif abs(len(alb) - len(a)) <= 3:
            ratio = difflib.SequenceMatcher(None, a, alb).ratio()
        else:
            continue
        if ratio < 0.9:
            continue
        for e in ents:
            artist_ok = not av or bool(av & e["artists"])
            if ratio == 1.0 and artist_ok:
                exact.append(e)
            elif artist_ok or ratio == 1.0:
                likely.append(e)
    return exact, likely


def _to_match(exact, likely):
    chosen, level = (exact, "exact") if exact else (likely, "likely") if likely else ([], "none")
    if not chosen:
        return Match("none")
    return Match(level, sorted({e["dir"] for e in chosen})[:5], sum(e["tracks"] for e in chosen),
                 all(e["lossless"] for e in chosen))


class Catalog:
    def __init__(self, path: str):
        self.path = path
        self._mtime = None
        self._index: dict = {}

    def mtime(self):
        try:
            return os.stat(self.path).st_mtime
        except OSError:
            return None

    def _load(self):
        m = os.stat(self.path).st_mtime
        if m == self._mtime:
            return
        db = sqlite3.connect(Path(self.path).resolve().as_uri() + "?mode=ro", uri=True)
        try:
            rows = db.execute("SELECT relative_path, is_lossless FROM tracks").fetchall()
        finally:
            db.close()
        dirs: dict = {}
        for rp, lossless in rows:
            if not isinstance(rp, str):
                continue
            parts = rp.split("/")
            if len(parts) < 5:
                continue
            d = "/".join(parts[:-1])
            e = dirs.setdefault(d, {"dir": d, "artists": artist_variants(parts[2]), "album": norm(parts[3]),
                                    "tracks": 0, "lossless": True})
            e["tracks"] += 1
            e["lossless"] = e["lossless"] and bool(lossless)
        index: dict = {}
        for e in dirs.values():
            index.setdefault(e["album"], []).append(e)
        self._index, self._mtime = index, m

    def match(self, artist: str, album: str) -> Match:
        try:
            self._load()
        except (sqlite3.Error, OSError) as e:
            return Match("unavailable", note=str(e))
        return _to_match(*_rank(self._index.items(), artist, album))


def staging_match(root: str, artist: str, album: str) -> Match:
    entries: dict = {}
    try:
        for a in os.scandir(root):
            if a.name.startswith(".") or not a.is_dir():
                continue
            for b in os.scandir(a.path):
                if b.is_dir():
                    n = sum(1 for f in os.scandir(b.path) if f.is_file())
                    entries.setdefault(norm(b.name), []).append(
                        {"dir": f"{a.name}/{b.name}", "artists": artist_variants(a.name), "tracks": n, "lossless": False})
    except OSError:
        return Match("none")
    return _to_match(*_rank(entries.items(), artist, album))
