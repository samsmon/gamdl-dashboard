import re
from dataclasses import dataclass

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_LEVEL = re.compile(r"^\[(DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+[\d:]+\]\s*(.*)$")
_PROGRESS = re.compile(r"^\[download\]\s+(\d+(?:\.\d+)?)%\s+of\s+~?\s*(\S+)(?:\s+at\s+(\S+))?")
_TRACK_DELAY = re.compile(r"^\[gamdl-safe\]\s+track delay\s+(\d+)s")
_ALBUM_DELAY = re.compile(r"^\[gamdl-safe\]\s+album delay\s+(\d+)s")
_URL = re.compile(r'URL\s+(\d+)/(\d+)\s+Processing\s+"?(.*?)"?\s*$')
_TRACK = re.compile(r'\[Track\s+(\d+)/(\d+)\s*\]\s*(Downloading|Skipping)\s+"(.*?)"(?::\s*(.*))?$')
_ERRDL = re.compile(r'Error downloading\s+"(.*?)"')
_FINISHED = re.compile(r"Finished with (\d+) error")
_RATE = re.compile(r"\b(?:429|403)\b|too many requests|rate.?limit", re.I)
_AUTH = re.compile(r"\b401\b|cookies?|not (?:logged|signed) in|subscription|unauthori[sz]ed", re.I)
_QUOTED = re.compile(r'"[^"]*"')


@dataclass(frozen=True)
class UrlStart:
    n: int
    total: int
    url: str


@dataclass(frozen=True)
class TrackStart:
    i: int
    total: int
    title: str


@dataclass(frozen=True)
class TrackSkip:
    i: int
    total: int
    title: str
    reason: str


@dataclass(frozen=True)
class TrackError:
    title: str


@dataclass(frozen=True)
class Progress:
    pct: float
    size: str
    speed: str | None


@dataclass(frozen=True)
class TrackDelay:
    seconds: int


@dataclass(frozen=True)
class AlbumDelay:
    seconds: int


@dataclass(frozen=True)
class Finished:
    errors: int


@dataclass(frozen=True)
class Line:
    level: str
    text: str
    cls: str


def classify(level: str, body: str) -> str:
    """Only WARNING/ERROR/CRITICAL lines can be rate-limit or auth; quoted titles are ignored."""
    if level not in ("WARNING", "ERROR", "CRITICAL"):
        return "other"
    scrubbed = _QUOTED.sub('""', body)
    if _RATE.search(scrubbed):
        return "rate_limit"
    if _AUTH.search(scrubbed):
        return "auth"
    return "other"


def parse_line(raw: str) -> list:
    text = ANSI.sub("", raw).strip()
    if not text:
        return []
    m = _PROGRESS.match(text)
    if m:
        return [Progress(float(m[1]), m[2], m[3])]
    events: list = []
    if (m := _TRACK_DELAY.match(text)):
        events.append(TrackDelay(int(m[1])))
    elif (m := _ALBUM_DELAY.match(text)):
        events.append(AlbumDelay(int(m[1])))
    level, body = "INFO", text
    lm = _LEVEL.match(text)
    if lm:
        level, body = lm[1], lm[2]
    if (m := _URL.search(body)):
        events.append(UrlStart(int(m[1]), int(m[2]), m[3]))
    elif (m := _TRACK.search(body)):
        i, total, verb, title, reason = int(m[1]), int(m[2]), m[3], m[4], m[5] or ""
        events.append(TrackStart(i, total, title) if verb == "Downloading" else TrackSkip(i, total, title, reason))
    elif (m := _ERRDL.search(body)):
        events.append(TrackError(m[1]))
    if (m := _FINISHED.search(body)):
        events.append(Finished(int(m[1])))
    events.append(Line(level, text, classify(level, body)))
    return events


class LineSplitter:
    """Split a text stream on \\r and \\n, keeping the partial tail between reads."""

    def __init__(self):
        self._buf = ""

    def feed(self, data: str) -> list:
        self._buf += data
        parts = re.split(r"[\r\n]+", self._buf)
        self._buf = parts.pop()
        return [p for p in parts if p]

    def flush(self) -> list:
        rest, self._buf = self._buf, ""
        return [rest] if rest else []
