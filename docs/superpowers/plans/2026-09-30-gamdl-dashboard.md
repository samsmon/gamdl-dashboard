# gamdl Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A monochrome, qBittorrent-style web dashboard on port 8110 that queues Apple Music URLs and runs them strictly one at a time through `gamdl-safe`, with rate-limit protection.

**Architecture:** A FastAPI app with one asyncio worker (`runner.py`) that is the only code that spawns `gamdl-safe`. Pure modules (`urls`, `parser`, `guard`, `settings`) hold the logic and are tested without I/O. SQLite is the source of truth; Server-Sent Events only tell the browser to refresh. A vanilla JS/CSS frontend needs no build step.

**Tech Stack:** Python 3.11+, FastAPI, uvicorn, httpx, stdlib `sqlite3`, pytest + pytest-asyncio, vanilla HTML/CSS/JS.

**Spec:** `docs/superpowers/specs/2026-09-30-gamdl-dashboard-design.md` (read it first). Wrapper source: `C:\Users\Sam\Documents\GitHub\homelab-ops\scripts\gamdl-safe.py` (read-only reference; never edit that repo from here).

## Global Constraints

- Exactly one `gamdl-safe` process at a time. The runner is a single worker guarded by an `asyncio.Lock`; the wrapper flock is only a second net.
- **Album delay is the dashboard's job.** The wrapper only delays "before each URL after the first" *inside one process*. The dashboard runs one URL per process, so the runner must itself wait a random `album_delay` between items (default `60-180` s).
- Delays are never below the wrapper-default floors: track `5` s, album `30` s (validated in settings).
- Auto-pause after `error_threshold` (default 3) consecutive 429/403 lines; resume is manual only. Auth errors pause with banner `cookies`.
- Cookie contents are never stored, logged or returned. `cookies.py` returns only ages and an expiry-in-days integer.
- Every URL is normalized to the `jp` storefront: `https://music.apple.com/jp/<type>/<id>` (slug dropped). Storefront is the setting `storefront`, default `jp`. Also passed to the wrapper as `GAMDL_STOREFRONT`.
- Wrapper env: `GAMDL_TRACK_DELAY`, `GAMDL_ALBUM_DELAY` (format `"min-max"` seconds), `GAMDL_STOREFRONT`. Wrapper lock-failure text contains `already running`.
- `catalog.sqlite` is opened read-only (`mode=ro`) and never written.
- No deployment, no SSH, no writes to homelab-ops or to the server. No real Apple/network calls in tests.
- Frontend: greys only, no accent color, no gradients/shadows/emoji/icons-in-circles, monospace system font, square corners, status by glyph+fill pattern (never hue). All server text is inserted with `textContent` (never `innerHTML`).
- Default bind `0.0.0.0:8110` via `GAMDL_DASH_HOST` / `GAMDL_DASH_PORT`.
- Setting `storefront` may be empty (= no rewrite, passed to the wrapper as an empty `GAMDL_STOREFRONT`, which disables its rewrite).
- Library check (spec 8.4) is read-only on `metadata.csv` and `catalog.sqlite`. `metadata.csv` `Path` values are old Windows paths and are never joined to `catalog.sqlite`. Score weights: title 0.35, duration 0.25, track overlap 0.25, track count 0.15, artist bonus 0.05; fewer than 3 components caps the score at 0.80; thresholds `library_exact` 0.85 / `library_similar` 0.55.
- Retry (spec 8.9): album-level re-run for non-rate-limit, non-auth errors, `track_retries` default 2, `retry_backoff` default `120-300` s (floor 30). The guard verdict always wins; no retry after 429/403/auth.
- Codec comes from `ffprobe`, never the extension. No file is ever moved, renamed or converted.
- Albums with more than one track get a collapsed-by-default inline track list; single-track items get none (spec 8.10).
- Git: commit as Maja, plain messages, **no Co-Authored-By trailer, no Claude attribution**.

## Review Focus

1. Pasted text with mixed separators, trailing spaces, uppercase storefront, percent-encoded CJK slug, `?i=` song links, duplicates, and non-Apple URLs: valid lines still queue, bad lines report a reason (Task 2).
2. A `\r`-terminated yt-dlp progress line split across two reads must still parse (Task 3).
3. A song titled "Room 403" or "429 Days" in a normal or quoted log line must NOT count as a rate-limit error (Task 3, Task 4).
4. Killing the dashboard mid-download: on restart the item returns to `queued`, the queue starts paused, and nothing re-runs by itself (Tasks 5, 10).
5. `metadata.csv` / `catalog.sqlite` missing, locked, malformed or with blank durations: the row shows "library data unavailable" or a capped score, and never blocks queueing; a title-only match must never reach `in library` (Tasks 7, 7b).
6b. A non-rate-limit track failure (stall/timeout) is retried after backoff and then succeeds; a 429 is never retried automatically (Task 10).
6c. A single-track download shows no expand toggle; an album does, collapsed by default (Task 12).
6. Cancel while paused, pause while idle, and resume after a rate-limit pause must not leave a stuck `downloading` item or a stale banner (Task 10).

---

### Task 1: Scaffold and config

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `app/__init__.py`, `app/config.py`, `tests/conftest.py`, `tests/test_config.py`

**Interfaces:**
- Produces: `app.config.Config` dataclass and `app.config.from_env(env: Mapping[str,str] | None = None) -> Config`.
  Fields: `db_path:str, gamdl_cmd:list[str], extra_args:list[str], staging_dir:str, disk_path:str, cookies_path:str, catalog_path:str, host:str, port:int, autostart:bool`.

- [ ] **Step 1: Create project files**

`pyproject.toml`:
```toml
[project]
name = "gamdl-dashboard"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["fastapi>=0.110", "uvicorn>=0.29", "httpx>=0.27"]

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-asyncio>=0.23"]

[tool.setuptools]
packages = ["app"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
pythonpath = ["."]
```

`.gitignore`:
```
.venv/
__pycache__/
*.pyc
*.sqlite
*.sqlite-journal
data/
.pytest_cache/
```

`app/__init__.py`: empty file.

`tests/conftest.py`:
```python
import pytest

from app.store import Store  # created in Task 5; tests that use `store` come after it


@pytest.fixture
def store(tmp_path):
    return Store(str(tmp_path / "t.sqlite"))
```
(Until Task 5 exists this import fails; so for this task write conftest with only `import pytest` and add the fixture in Task 5 Step 3.)

- [ ] **Step 2: Write the failing test** `tests/test_config.py`
```python
from app.config import from_env


def test_defaults():
    c = from_env({})
    assert c.port == 8110
    assert c.host == "0.0.0.0"
    assert c.gamdl_cmd == ["/usr/local/bin/gamdl-safe"]
    assert c.extra_args == ["--no-exceptions"]
    assert c.staging_dir == "/mnt/hdd-backup/music/_gamdl-incoming"
    assert c.library_csv == "/mnt/hdd-backup/music/metadata.csv"
    assert c.autostart is True


def test_overrides():
    c = from_env({
        "GAMDL_DASH_PORT": "9000",
        "GAMDL_DASH_GAMDL_CMD": "py -3 tools/fake_gamdl_safe.py",
        "GAMDL_DASH_EXTRA_ARGS": "",
        "GAMDL_DASH_AUTOSTART": "0",
    })
    assert c.port == 9000
    assert c.gamdl_cmd == ["py", "-3", "tools/fake_gamdl_safe.py"]
    assert c.extra_args == []
    assert c.autostart is False
```

- [ ] **Step 3: Set up venv and run to see it fail**

Run:
```bash
py -3 -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/python -m pytest tests/test_config.py -v
```
Expected: FAIL `ModuleNotFoundError: app.config`.

- [ ] **Step 4: Implement** `app/config.py`
```python
import os
import shlex
from dataclasses import dataclass
from typing import Mapping


@dataclass
class Config:
    db_path: str
    gamdl_cmd: list
    extra_args: list
    staging_dir: str
    disk_path: str
    cookies_path: str
    catalog_path: str
    host: str
    port: int
    autostart: bool
    library_csv: str = ""


def _split(value: str) -> list:
    return shlex.split(value, posix=(os.name != "nt"))


def from_env(env: Mapping[str, str] | None = None) -> Config:
    e = os.environ if env is None else env
    return Config(
        db_path=e.get("GAMDL_DASH_DB", "data/dashboard.sqlite"),
        gamdl_cmd=_split(e.get("GAMDL_DASH_GAMDL_CMD", "/usr/local/bin/gamdl-safe")),
        extra_args=_split(e.get("GAMDL_DASH_EXTRA_ARGS", "--no-exceptions")),
        staging_dir=e.get("GAMDL_DASH_STAGING", "/mnt/hdd-backup/music/_gamdl-incoming"),
        disk_path=e.get("GAMDL_DASH_DISK_PATH", "/mnt/hdd-backup"),
        cookies_path=e.get("GAMDL_DASH_COOKIES", "/root/.gamdl/cookies.txt"),
        catalog_path=e.get("GAMDL_DASH_CATALOG", "/mnt/hdd-backup/music/catalog.sqlite"),
        host=e.get("GAMDL_DASH_HOST", "0.0.0.0"),
        port=int(e.get("GAMDL_DASH_PORT", "8110")),
        autostart=e.get("GAMDL_DASH_AUTOSTART", "1") not in ("0", "false", "no"),
        library_csv=e.get("GAMDL_DASH_LIBRARY_CSV", "/mnt/hdd-backup/music/metadata.csv"),
    )
```

- [ ] **Step 5: Run tests, expect PASS, commit**
```bash
.venv/Scripts/python -m pytest tests/test_config.py -v
git add -A && git commit -m "feat: project scaffold and env config"
```

---

### Task 2: URL normalization (`/jp/`) and multi-URL parsing

**Files:**
- Create: `app/urls.py`, `tests/test_urls.py`

**Interfaces:**
- Produces:
  - `class UrlError(ValueError)`
  - `@dataclass(frozen=True) ParsedUrl(kind:str, id:str, track_id:str|None, storefront:str, original:str, normalized:str)` with property `key -> tuple` = `(kind, id, track_id)`.
  - `normalize(raw:str, storefront:str|None="jp") -> ParsedUrl` (`None` keeps the URL's own storefront).
  - `@dataclass UrlResult(raw:str, parsed:ParsedUrl|None, error:str|None)`
  - `parse_many(text:str, storefront:str|None="jp") -> list[UrlResult]` (splits on whitespace/commas, in-paste duplicates get `error="duplicate"`).

- [ ] **Step 1: Write the failing tests** `tests/test_urls.py`
```python
import pytest

from app.urls import UrlError, normalize, parse_many

JP = "https://music.apple.com/jp/album/%E6%BA%9C%E6%81%AF/1791035368"
ID = "https://music.apple.com/id/album/frozen-flower/1851922484"


def test_jp_url_normalizes_and_drops_slug():
    p = normalize(JP)
    assert p.normalized == "https://music.apple.com/jp/album/1791035368"
    assert p.kind == "album" and p.id == "1791035368" and p.storefront == "jp"


def test_id_storefront_is_rewritten_to_jp():
    p = normalize(ID)
    assert p.normalized == "https://music.apple.com/jp/album/1851922484"
    assert p.original == ID


def test_uppercase_storefront_and_whitespace():
    p = normalize("  https://music.apple.com/US/album/x/123  ")
    assert p.normalized == "https://music.apple.com/jp/album/123"


def test_keep_original_storefront_when_none():
    assert normalize(ID, storefront=None).normalized == "https://music.apple.com/id/album/1851922484"


def test_empty_storefront_disables_rewrite():
    assert normalize(ID, storefront="").normalized == "https://music.apple.com/id/album/1851922484"


def test_song_in_album_keeps_track_id():
    p = normalize("https://music.apple.com/us/album/x/123?i=456")
    assert p.track_id == "456"
    assert p.normalized == "https://music.apple.com/jp/album/123?i=456"
    assert p.key == ("album", "123", "456")


def test_playlist_id():
    p = normalize("https://music.apple.com/us/playlist/mix/pl.u-abc123")
    assert p.normalized == "https://music.apple.com/jp/playlist/pl.u-abc123"


@pytest.mark.parametrize("bad", [
    "https://example.com/jp/album/1",
    "ftp://music.apple.com/jp/album/1",
    "https://music.apple.com/jp/album/notanumber",
    "https://music.apple.com/jp/video/1",
    "https://music.apple.com/jp",
    "not a url",
])
def test_rejects_bad_urls(bad):
    with pytest.raises(UrlError):
        normalize(bad)


def test_parse_many_mixed_separators_and_duplicates():
    text = f"{JP}\n{ID}, {JP}   https://example.com/x"
    r = parse_many(text)
    assert [x.error is None for x in r] == [True, True, False, False]
    assert r[2].error == "duplicate"
    assert "Apple Music" in r[3].error
    assert r[0].parsed.normalized.endswith("/1791035368")


def test_parse_many_empty():
    assert parse_many("  \n ") == []
```

- [ ] **Step 2: Run, expect FAIL** (`ModuleNotFoundError: app.urls`)

Run: `.venv/Scripts/python -m pytest tests/test_urls.py -v`

- [ ] **Step 3: Implement** `app/urls.py`
```python
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
```

- [ ] **Step 4: Run tests, expect PASS; commit**
```bash
.venv/Scripts/python -m pytest tests/test_urls.py -v
git add -A && git commit -m "feat: normalize apple music urls to jp storefront"
```

---

### Task 3: Log parser

**Files:**
- Create: `app/parser.py`, `tests/test_parser.py`

**Interfaces:**
- Produces (all frozen dataclasses): `UrlStart(n,total,url)`, `TrackStart(i,total,title)`, `TrackSkip(i,total,title,reason)`, `TrackError(title)`, `Progress(pct:float,size:str,speed:str|None)`, `TrackDelay(seconds:int)`, `AlbumDelay(seconds:int)`, `Finished(errors:int)`, `Line(level:str,text:str,cls:str)` where `cls in {"rate_limit","auth","other"}`.
- `parse_line(raw:str) -> list[Event]`: strips ANSI; returns `[]` for blank lines; progress lines return only `Progress`; every other line returns its specific event(s) plus a `Line`.
- `class LineSplitter`: `feed(text:str) -> list[str]` splits on `\r` and `\n` keeping a partial tail; `flush() -> list[str]`.

- [ ] **Step 1: Write the failing tests** `tests/test_parser.py`
```python
from app.parser import (AlbumDelay, Finished, Line, LineSplitter, Progress, TrackDelay,
                        TrackError, TrackSkip, TrackStart, UrlStart, parse_line)


def kinds(evs):
    return [type(e).__name__ for e in evs]


def test_track_start_with_ansi():
    evs = parse_line('\x1b[32m[INFO     19:03:47]\x1b[0m [Track   1/17 ] Downloading "The City Where Whales Fall"')
    ts = evs[0]
    assert isinstance(ts, TrackStart)
    assert (ts.i, ts.total, ts.title) == (1, 17, "The City Where Whales Fall")


def test_skip():
    evs = parse_line('[WARNING  19:04:00] [Track   6/17 ] Skipping "End Roll": file exists')
    s = evs[0]
    assert isinstance(s, TrackSkip) and s.reason == "file exists" and s.i == 6


def test_url_start_and_finished():
    u = parse_line('[INFO     19:00:00] URL   1/3  Processing "https://music.apple.com/jp/album/1"')[0]
    assert isinstance(u, UrlStart) and (u.n, u.total) == (1, 3)
    f = parse_line("[INFO     19:08:12] Finished with 2 error(s)")[0]
    assert isinstance(f, Finished) and f.errors == 2


def test_delays():
    assert parse_line("[gamdl-safe] track delay 5s")[0] == TrackDelay(5)
    assert parse_line("[gamdl-safe] album delay 87s")[0] == AlbumDelay(87)


def test_progress_only_progress_event():
    evs = parse_line("[download]  4.4% of ~ 22.57KiB at 8.49KiB/s (frag 0/23)")
    assert kinds(evs) == ["Progress"]
    assert evs[0].pct == 4.4 and evs[0].size == "22.57KiB" and evs[0].speed == "8.49KiB/s"


def test_progress_without_speed():
    evs = parse_line("[download] 100% of 5.00MiB")
    assert evs[0].pct == 100.0 and evs[0].speed is None


def test_error_classification():
    e = parse_line('[ERROR    19:00:00] Error downloading "X": HTTP 429 Too Many Requests')
    assert any(isinstance(x, TrackError) for x in e)
    line = [x for x in e if isinstance(x, Line)][0]
    assert line.level == "ERROR" and line.cls == "rate_limit"
    a = parse_line("[ERROR    19:00:00] 401 Unauthorized: invalid cookies")
    assert [x for x in a if isinstance(x, Line)][0].cls == "auth"


def test_titles_with_numbers_are_not_rate_limits():
    evs = parse_line('[WARNING  19:00:00] [Track   2/9 ] Skipping "Room 403": already exists')
    assert [x for x in evs if isinstance(x, Line)][0].cls == "other"
    evs = parse_line('[ERROR    19:00:00] Error downloading "429 Days": timeout')
    assert [x for x in evs if isinstance(x, Line)][0].cls == "other"


def test_info_lines_never_classified():
    evs = parse_line("[INFO     19:00:00] retrying after 429")
    assert [x for x in evs if isinstance(x, Line)][0].cls == "other"


def test_blank_and_garbage_are_safe():
    assert parse_line("   ") == []
    assert kinds(parse_line("\x00\x01 weird")) == ["Line"]


def test_splitter_handles_cr_and_split_reads():
    s = LineSplitter()
    assert s.feed("[download]  1.0% of 5MiB\r[download]  2.") == ["[download]  1.0% of 5MiB"]
    assert s.feed("0% of 5MiB\rnext\nlast") == ["[download]  2.0% of 5MiB", "next"]
    assert s.flush() == ["last"]
    assert s.flush() == []
```

- [ ] **Step 2: Run, expect FAIL** (`ModuleNotFoundError: app.parser`)

- [ ] **Step 3: Implement** `app/parser.py`
```python
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
```

- [ ] **Step 4: Run tests, expect PASS; commit**
```bash
.venv/Scripts/python -m pytest tests/test_parser.py -v
git add -A && git commit -m "feat: gamdl log parser with rate-limit classification"
```

---

### Task 4: Guard and settings validation

**Files:**
- Create: `app/guard.py`, `app/settings.py`, `tests/test_guard.py`, `tests/test_settings.py`

**Interfaces:**
- Consumes: event classes from `app.parser`.
- Produces:
  - `Verdict(kind:str, reason:str)` with `kind in {"rate_limited","cookies"}`; `Guard(threshold:int)` with attribute `consecutive:int` and `on_event(ev) -> Verdict|None`, `reset()`.
  - `settings.DEFAULTS: dict`, `settings.parse_range(s:str) -> tuple[float,float]`, `settings.validate(patch:dict) -> dict` (returns cleaned patch, raises `ValueError` with a readable message).

- [ ] **Step 1: Write the failing tests**

`tests/test_guard.py`:
```python
from app.guard import Guard
from app.parser import Line, TrackDelay


def err(cls, level="ERROR"):
    return Line(level, "x", cls)


def test_three_consecutive_rate_limits_pause():
    g = Guard(3)
    assert g.on_event(err("rate_limit")) is None
    assert g.on_event(err("rate_limit")) is None
    v = g.on_event(err("rate_limit"))
    assert v.kind == "rate_limited" and "3" in v.reason


def test_success_resets_count():
    g = Guard(3)
    g.on_event(err("rate_limit")); g.on_event(err("rate_limit"))
    g.on_event(TrackDelay(5))
    assert g.on_event(err("rate_limit")) is None
    assert g.consecutive == 1


def test_auth_pauses_immediately_with_cookie_verdict():
    assert Guard(3).on_event(err("auth")).kind == "cookies"


def test_other_and_info_lines_ignored():
    g = Guard(1)
    assert g.on_event(err("other")) is None
    assert g.on_event(Line("INFO", "x", "other")) is None


def test_reset():
    g = Guard(2)
    g.on_event(err("rate_limit"))
    g.reset()
    assert g.consecutive == 0
```

`tests/test_settings.py`:
```python
import pytest

from app.settings import DEFAULTS, check_merged, parse_range, validate


def test_defaults_present():
    assert DEFAULTS["track_delay"] == "8-20"
    assert DEFAULTS["album_delay"] == "60-180"
    assert DEFAULTS["error_threshold"] == 3
    assert DEFAULTS["max_tracks_per_24h"] == 150
    assert DEFAULTS["storefront"] == "jp"
    assert DEFAULTS["library_exact"] == 0.85 and DEFAULTS["library_similar"] == 0.55
    assert DEFAULTS["track_retries"] == 2 and DEFAULTS["retry_backoff"] == "120-300"


def test_empty_storefront_allowed_and_cross_field_rule():
    assert validate({"storefront": ""}) == {"storefront": ""}
    with pytest.raises(ValueError):
        check_merged({**DEFAULTS, "library_similar": 0.9, "library_exact": 0.8})
    check_merged(DEFAULTS)


def test_parse_range():
    assert parse_range("8-20") == (8.0, 20.0)


@pytest.mark.parametrize("patch", [
    {"track_delay": "2-4"},          # below 5 s floor
    {"album_delay": "10-20"},        # below 30 s floor
    {"track_delay": "20-8"},         # min > max
    {"track_delay": "abc"},
    {"error_threshold": 0},
    {"storefront": "japan"},
    {"max_tracks_per_24h": -1},
    {"retry_backoff": "10-20"},      # below 30 s floor
    {"track_retries": 99},
    {"library_exact": 1.5},
    {"unknown_key": 1},
])
def test_rejects(patch):
    with pytest.raises(ValueError):
        validate(patch)


def test_accepts_and_cleans():
    out = validate({"track_delay": " 10-30 ", "error_threshold": "5", "storefront": "JP", "auto_resume_after_cap": True})
    assert out == {"track_delay": "10-30", "error_threshold": 5, "storefront": "jp", "auto_resume_after_cap": True}
```

- [ ] **Step 2: Run, expect FAIL** (`ModuleNotFoundError`)

- [ ] **Step 3: Implement**

`app/guard.py`:
```python
from dataclasses import dataclass

from app.parser import Line, TrackDelay


@dataclass(frozen=True)
class Verdict:
    kind: str
    reason: str


class Guard:
    def __init__(self, threshold: int):
        self.threshold = threshold
        self.consecutive = 0

    def reset(self) -> None:
        self.consecutive = 0

    def on_event(self, ev):
        if isinstance(ev, TrackDelay):
            self.consecutive = 0
            return None
        if isinstance(ev, Line) and ev.level in ("WARNING", "ERROR", "CRITICAL"):
            if ev.cls == "auth":
                return Verdict("cookies", "authentication failed: re-export cookies.txt")
            if ev.cls == "rate_limit":
                self.consecutive += 1
                if self.consecutive >= self.threshold:
                    return Verdict("rate_limited", f"{self.consecutive} consecutive 429/403 errors")
        return None
```

`app/settings.py`:
```python
import re

TRACK_FLOOR = 5.0
ALBUM_FLOOR = 30.0

DEFAULTS = {
    "track_delay": "8-20",
    "album_delay": "60-180",
    "error_threshold": 3,
    "low_disk_gb": 20,
    "max_tracks_per_24h": 150,
    "auto_resume_after_cap": False,
    "storefront": "jp",
    "preview_max_tracks": 100,
    "library_exact": 0.85,
    "library_similar": 0.55,
    "track_retries": 2,
    "retry_backoff": "120-300",
}


def parse_range(s: str) -> tuple:
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*", str(s))
    if not m:
        raise ValueError(f"bad range {s!r}, expected 'min-max' seconds")
    lo, hi = float(m[1]), float(m[2])
    if lo > hi:
        raise ValueError(f"range {s!r}: min is greater than max")
    return lo, hi


def _range(key: str, floor: float):
    def check(v):
        lo, hi = parse_range(v)
        if lo < floor:
            raise ValueError(f"{key}: minimum must be at least {floor:g} s")
        return f"{lo:g}-{hi:g}"
    return check


def _int(key: str, lo: int, hi: int):
    def check(v):
        try:
            n = int(v)
        except (TypeError, ValueError):
            raise ValueError(f"{key}: must be an integer")
        if not lo <= n <= hi:
            raise ValueError(f"{key}: must be between {lo} and {hi}")
        return n
    return check


def _bool(key: str):
    def check(v):
        if isinstance(v, bool):
            return v
        raise ValueError(f"{key}: must be true or false")
    return check


def _storefront(v):
    v = str(v).strip().lower()
    if v and not re.fullmatch(r"[a-z]{2}", v):
        raise ValueError("storefront: must be a 2-letter code, or empty to disable the rewrite")
    return v


def _score(key: str):
    def check(v):
        try:
            n = float(v)
        except (TypeError, ValueError):
            raise ValueError(f"{key}: must be a number")
        if not 0.0 < n <= 1.0:
            raise ValueError(f"{key}: must be between 0 and 1")
        return n
    return check


RETRY_FLOOR = 30.0


def check_merged(s: dict) -> None:
    """Cross-field rules, applied to the full settings dict after a patch is merged."""
    if not s["library_similar"] < s["library_exact"]:
        raise ValueError("library_similar must be lower than library_exact")


_RULES = {
    "library_exact": _score("library_exact"),
    "library_similar": _score("library_similar"),
    "track_retries": _int("track_retries", 0, 10),
    "retry_backoff": _range("retry_backoff", RETRY_FLOOR),
    "track_delay": _range("track_delay", TRACK_FLOOR),
    "album_delay": _range("album_delay", ALBUM_FLOOR),
    "error_threshold": _int("error_threshold", 1, 20),
    "low_disk_gb": _int("low_disk_gb", 0, 2000),
    "max_tracks_per_24h": _int("max_tracks_per_24h", 0, 5000),
    "auto_resume_after_cap": _bool("auto_resume_after_cap"),
    "storefront": _storefront,
    "preview_max_tracks": _int("preview_max_tracks", 1, 5000),
}


def validate(patch: dict) -> dict:
    out = {}
    for k, v in patch.items():
        if k not in _RULES:
            raise ValueError(f"unknown setting: {k}")
        out[k] = _RULES[k](v)
    return out
```
Note: `parse_range` is called with the *stripped* string in `_range` (regex allows spaces).

- [ ] **Step 4: Run tests, expect PASS; commit**
```bash
.venv/Scripts/python -m pytest tests/test_guard.py tests/test_settings.py -v
git add -A && git commit -m "feat: rate-limit guard and validated settings"
```

---

### Task 5: SQLite store

**Files:**
- Create: `app/store.py`, `tests/test_store.py`
- Modify: `tests/conftest.py` (add the `store` fixture)

**Interfaces:**
- Consumes: `app.settings.DEFAULTS`, `app.settings.validate`.
- Produces `class Store(path:str)`, all methods synchronous and thread-safe (one lock):
  - Items: `add_item(url, original_url, kind, ext_id, track_id=None, title=None, artist=None, expected_tracks=None) -> int`, `get_item(id)->dict|None`, `list_items()->list[dict]`, `find_by_key(kind, ext_id, track_id)->dict|None` (ignores `cancelled`), `update_item(id, **fields)` (whitelisted columns), `next_queued(now:float|None=None)->dict|None` (only items with `not_before` empty or <= now), `reorder(ids:list[int])`, `remove_item(id)`, `recover_after_crash()->int`.
  - Item columns: `id,url,original_url,kind,ext_id,track_id,status,position,title,artist,expected_tracks,url_i,url_n,track_i,track_n,current_title,errors,output_path,size_bytes,error_msg,findings,created_at,started_at,finished_at`. `findings` is a JSON string (list of str) or `NULL`.
  - Tracks: `clear_tracks(item_id)`, `upsert_track(item_id, idx, title, status, reason=None)`, `list_tracks(item_id)->list[dict]`.
  - Log: `add_log(item_id, level, text, ts)`, `tail_log(limit=200)->list[dict]` (oldest first; capped to 2000 rows stored).
  - Cap: `record_track(ts)`, `count_tracks_since(ts)->int`, `oldest_track_since(ts)->float|None`.
  - Settings/flags: `get_settings()->dict`, `put_settings(patch)->dict`, `get_flag(key)->str|None`, `set_flag(key, value|None)`, `get_banner()->dict|None`, `set_banner(kind, reason)`, `clear_banner()`.
  - History: `history_totals()->tuple[int,int]` (bytes, tracks over `done` items), `done_items()->list[dict]` (newest first).

- [ ] **Step 1: Add fixture and write failing tests**

Replace `tests/conftest.py` with:
```python
import pytest

from app.store import Store


@pytest.fixture
def store(tmp_path):
    return Store(str(tmp_path / "t.sqlite"))
```

`tests/test_store.py`:
```python
import json

from app.store import Store


def add(store, n, kind="album"):
    return store.add_item(f"https://music.apple.com/jp/{kind}/{n}", f"orig{n}", kind, str(n))


def test_add_list_order_and_next(store):
    a, b = add(store, 1), add(store, 2)
    assert [i["id"] for i in store.list_items()] == [a, b]
    assert store.next_queued()["id"] == a
    store.update_item(a, status="done")
    assert store.next_queued()["id"] == b


def test_next_queued_honors_not_before(store):
    a, b = add(store, 1), add(store, 2)
    store.update_item(a, not_before=5000.0, attempts=1)
    assert store.next_queued(now=1000.0)["id"] == b      # a is backing off, b goes first
    store.update_item(b, status="done")
    assert store.next_queued(now=1000.0) is None
    assert store.next_queued(now=6000.0)["id"] == a


def test_new_columns_default(store):
    a = add(store, 1)
    it = store.get_item(a)
    assert it["attempts"] == 0 and it["not_before"] is None and it["codec"] is None
    store.update_item(a, codec="aac 256k", classification="Lossy/ [AAC 256k]", library_status="new")
    assert store.get_item(a)["classification"] == "Lossy/ [AAC 256k]"


def test_reorder(store):
    a, b, c = add(store, 1), add(store, 2), add(store, 3)
    store.reorder([c, a, b])
    assert [i["id"] for i in store.list_items()] == [c, a, b]
    assert store.next_queued()["id"] == c


def test_find_by_key_ignores_cancelled(store):
    a = add(store, 1)
    assert store.find_by_key("album", "1", None)["id"] == a
    store.update_item(a, status="cancelled")
    assert store.find_by_key("album", "1", None) is None


def test_update_rejects_unknown_column(store):
    a = add(store, 1)
    try:
        store.update_item(a, evil="x")
        assert False
    except ValueError:
        pass


def test_recover_after_crash(store):
    a, b = add(store, 1), add(store, 2)
    store.update_item(a, status="downloading")
    store.update_item(b, status="waiting")
    assert store.recover_after_crash() == 2
    assert {i["status"] for i in store.list_items()} == {"queued"}
    assert store.recover_after_crash() == 0


def test_tracks_and_clear(store):
    a = add(store, 1)
    store.upsert_track(a, 1, "T1", "downloading")
    store.upsert_track(a, 1, "T1", "done")
    store.upsert_track(a, 2, "T2", "skipped", "exists")
    rows = store.list_tracks(a)
    assert [(r["idx"], r["status"]) for r in rows] == [(1, "done"), (2, "skipped")]
    store.clear_tracks(a)
    assert store.list_tracks(a) == []


def test_log_tail_and_cap(store):
    for i in range(2100):
        store.add_log(None, "INFO", f"l{i}", float(i))
    tail = store.tail_log(3)
    assert [t["text"] for t in tail] == ["l2097", "l2098", "l2099"]
    assert len(store.tail_log(5000)) <= 2000


def test_track_counter(store):
    for t in (100.0, 200.0, 300.0):
        store.record_track(t)
    assert store.count_tracks_since(150.0) == 2
    assert store.oldest_track_since(150.0) == 200.0
    assert store.oldest_track_since(999.0) is None


def test_settings_and_flags_persist(tmp_path):
    p = str(tmp_path / "s.sqlite")
    s = Store(p)
    assert s.get_settings()["storefront"] == "jp"
    s.put_settings({"error_threshold": 5})
    s.set_flag("paused", "1")
    s.set_banner("rate_limited", "3 errors")
    s2 = Store(p)
    assert s2.get_settings()["error_threshold"] == 5
    assert s2.get_flag("paused") == "1"
    assert s2.get_banner() == {"kind": "rate_limited", "reason": "3 errors"}
    s2.clear_banner()
    assert s2.get_banner() is None
    s2.set_flag("paused", None)
    assert s2.get_flag("paused") is None


def test_history_totals(store):
    a, b = add(store, 1), add(store, 2)
    store.update_item(a, status="done", size_bytes=90, track_n=10, findings=json.dumps(["x"]))
    store.update_item(b, status="error", size_bytes=999, track_n=5)
    assert store.history_totals() == (90, 10)
    assert [i["id"] for i in store.done_items()] == [a]
```

- [ ] **Step 2: Run, expect FAIL** (`ModuleNotFoundError: app.store`)

- [ ] **Step 3: Implement** `app/store.py`
```python
import json
import sqlite3
import threading
import time

from app import settings as st

_ITEM_COLS = {
    "url", "original_url", "kind", "ext_id", "track_id", "status", "position", "title", "artist",
    "expected_tracks", "url_i", "url_n", "track_i", "track_n", "current_title", "errors",
    "output_path", "size_bytes", "error_msg", "findings", "created_at", "started_at", "finished_at",
    "attempts", "not_before", "codec", "classification", "library_status",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS items(
  id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT NOT NULL, original_url TEXT, kind TEXT, ext_id TEXT,
  track_id TEXT, status TEXT NOT NULL DEFAULT 'queued', position REAL NOT NULL DEFAULT 0,
  title TEXT, artist TEXT, expected_tracks INTEGER, url_i INTEGER, url_n INTEGER,
  track_i INTEGER, track_n INTEGER, current_title TEXT, errors INTEGER NOT NULL DEFAULT 0,
  output_path TEXT, size_bytes INTEGER, error_msg TEXT, findings TEXT,
  created_at REAL, started_at REAL, finished_at REAL,
  attempts INTEGER NOT NULL DEFAULT 0, not_before REAL, codec TEXT, classification TEXT, library_status TEXT);
CREATE TABLE IF NOT EXISTS tracks(
  item_id INTEGER NOT NULL, idx INTEGER NOT NULL, title TEXT, status TEXT, reason TEXT,
  PRIMARY KEY(item_id, idx));
CREATE TABLE IF NOT EXISTS log(id INTEGER PRIMARY KEY AUTOINCREMENT, item_id INTEGER, ts REAL, level TEXT, text TEXT);
CREATE TABLE IF NOT EXISTS finished_tracks(ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
"""


class Store:
    def __init__(self, path: str):
        if path != ":memory:":
            import os
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._db.executescript(_SCHEMA)
            self._db.commit()

    def _exec(self, sql, args=()):
        with self._lock:
            cur = self._db.execute(sql, args)
            self._db.commit()
            return cur

    def _all(self, sql, args=()):
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, args).fetchall()]

    def _one(self, sql, args=()):
        rows = self._all(sql, args)
        return rows[0] if rows else None

    # items
    def add_item(self, url, original_url, kind, ext_id, track_id=None, title=None, artist=None, expected_tracks=None):
        with self._lock:
            pos = self._db.execute("SELECT COALESCE(MAX(position),0)+1 FROM items").fetchone()[0]
        cur = self._exec(
            "INSERT INTO items(url,original_url,kind,ext_id,track_id,title,artist,expected_tracks,position,created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (url, original_url, kind, ext_id, track_id, title, artist, expected_tracks, pos, time.time()))
        return cur.lastrowid

    def get_item(self, item_id):
        return self._one("SELECT * FROM items WHERE id=?", (item_id,))

    def list_items(self):
        return self._all("SELECT * FROM items ORDER BY position, id")

    def find_by_key(self, kind, ext_id, track_id):
        return self._one(
            "SELECT * FROM items WHERE kind=? AND ext_id=? AND COALESCE(track_id,'')=COALESCE(?,'')"
            " AND status!='cancelled' LIMIT 1", (kind, ext_id, track_id))

    def update_item(self, item_id, **fields):
        bad = set(fields) - _ITEM_COLS
        if bad:
            raise ValueError(f"unknown item columns: {sorted(bad)}")
        if not fields:
            return
        sets = ",".join(f"{k}=?" for k in fields)
        self._exec(f"UPDATE items SET {sets} WHERE id=?", (*fields.values(), item_id))

    def next_queued(self, now=None):
        now = time.time() if now is None else now
        return self._one(
            "SELECT * FROM items WHERE status='queued' AND (not_before IS NULL OR not_before<=?)"
            " ORDER BY position, id LIMIT 1", (now,))

    def reorder(self, ids):
        with self._lock:
            for pos, i in enumerate(ids, start=1):
                self._db.execute("UPDATE items SET position=? WHERE id=?", (pos, i))
            self._db.commit()

    def remove_item(self, item_id):
        self._exec("DELETE FROM tracks WHERE item_id=?", (item_id,))
        self._exec("DELETE FROM items WHERE id=?", (item_id,))

    def recover_after_crash(self):
        cur = self._exec("UPDATE items SET status='queued' WHERE status IN ('downloading','waiting')")
        return cur.rowcount

    # tracks
    def clear_tracks(self, item_id):
        self._exec("DELETE FROM tracks WHERE item_id=?", (item_id,))

    def upsert_track(self, item_id, idx, title, status, reason=None):
        self._exec(
            "INSERT INTO tracks(item_id,idx,title,status,reason) VALUES(?,?,?,?,?)"
            " ON CONFLICT(item_id,idx) DO UPDATE SET title=excluded.title,status=excluded.status,reason=excluded.reason",
            (item_id, idx, title, status, reason))

    def list_tracks(self, item_id):
        return self._all("SELECT * FROM tracks WHERE item_id=? ORDER BY idx", (item_id,))

    # log
    def add_log(self, item_id, level, text, ts):
        cur = self._exec("INSERT INTO log(item_id,ts,level,text) VALUES(?,?,?,?)", (item_id, ts, level, text))
        self._exec("DELETE FROM log WHERE id<=?", (cur.lastrowid - 2000,))

    def tail_log(self, limit=200):
        rows = self._all("SELECT * FROM log ORDER BY id DESC LIMIT ?", (limit,))
        return list(reversed(rows))

    # cap
    def record_track(self, ts):
        self._exec("INSERT INTO finished_tracks(ts) VALUES(?)", (ts,))

    def count_tracks_since(self, ts):
        return self._one("SELECT COUNT(*) AS n FROM finished_tracks WHERE ts>=?", (ts,))["n"]

    def oldest_track_since(self, ts):
        r = self._one("SELECT MIN(ts) AS t FROM finished_tracks WHERE ts>=?", (ts,))
        return r["t"]

    # settings / flags
    def get_flag(self, key):
        r = self._one("SELECT v FROM kv WHERE k=?", (key,))
        return r["v"] if r else None

    def set_flag(self, key, value):
        if value is None:
            self._exec("DELETE FROM kv WHERE k=?", (key,))
        else:
            self._exec("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (key, value))

    def get_settings(self):
        raw = self.get_flag("settings")
        return {**st.DEFAULTS, **(json.loads(raw) if raw else {})}

    def put_settings(self, patch):
        clean = st.validate(patch)
        merged = {**self.get_settings(), **clean}
        st.check_merged(merged)
        self.set_flag("settings", json.dumps(merged))
        return merged

    def get_banner(self):
        raw = self.get_flag("banner")
        return json.loads(raw) if raw else None

    def set_banner(self, kind, reason):
        self.set_flag("banner", json.dumps({"kind": kind, "reason": reason}))

    def clear_banner(self):
        self.set_flag("banner", None)

    # history
    def history_totals(self):
        r = self._one("SELECT COALESCE(SUM(size_bytes),0) AS b, COALESCE(SUM(track_n),0) AS t FROM items WHERE status='done'")
        return r["b"], r["t"]

    def done_items(self):
        return self._all("SELECT * FROM items WHERE status='done' ORDER BY finished_at DESC, id DESC")
```

- [ ] **Step 4: Run tests, expect PASS; commit**
```bash
.venv/Scripts/python -m pytest -v
git add -A && git commit -m "feat: sqlite store for queue, tracks, log, settings"
```

---

### Task 6: Cookies, disk, forecast

**Files:**
- Create: `app/cookies.py`, `app/disk.py`, `app/forecast.py`, `tests/test_status_helpers.py`

**Interfaces:**
- Produces:
  - `cookies.cookie_status(path:str, now:float|None=None) -> dict` with keys `exists:bool, age_days:float|None, expiry_days:int|None, expired:bool`. It never returns or keeps names/values.
  - `disk.free_bytes(path:str) -> int|None`.
  - `forecast.DEFAULT_TRACK_BYTES = 9_000_000`; `forecast.avg_track_bytes(store)->int`; `forecast.estimate_bytes(store, item:dict)->int`; `forecast.queue_bytes(store)->int` (sum over `queued` items).

- [ ] **Step 1: Write the failing tests** `tests/test_status_helpers.py`
```python
import os
import time

from app.cookies import cookie_status
from app.disk import free_bytes
from app.forecast import DEFAULT_TRACK_BYTES, avg_track_bytes, estimate_bytes, queue_bytes

NOW = 2_000_000_000.0


def write_cookies(tmp_path, *expiries, extra=""):
    lines = ["# Netscape HTTP Cookie File"]
    for i, e in enumerate(expiries):
        lines.append(f".apple.com\tTRUE\t/\tTRUE\t{e}\tname{i}\tSECRET-VALUE-{i}")
    lines.append(f".example.com\tTRUE\t/\tTRUE\t{int(NOW) + 5}\tother\tzzz")
    lines.append(extra)
    p = tmp_path / "cookies.txt"
    p.write_text("\n".join(lines), encoding="utf-8")
    return str(p)


def test_missing_file(tmp_path):
    s = cookie_status(str(tmp_path / "nope.txt"), NOW)
    assert s == {"exists": False, "age_days": None, "expiry_days": None, "expired": False}


def test_earliest_expiry_only_apple_and_no_secrets(tmp_path):
    p = write_cookies(tmp_path, int(NOW) + 10 * 86400, int(NOW) + 3 * 86400)
    os.utime(p, (NOW - 86400, NOW - 86400))
    s = cookie_status(p, NOW)
    assert s["exists"] and s["expiry_days"] == 3 and not s["expired"]
    assert round(s["age_days"]) == 1
    assert "SECRET" not in repr(s)


def test_expired_and_httponly_and_session(tmp_path):
    p = write_cookies(tmp_path, int(NOW) - 100, 0, extra=f"#HttpOnly_.apple.com\tTRUE\t/\tTRUE\t{int(NOW)+86400*30}\tx\ty")
    s = cookie_status(p, NOW)
    assert s["expired"] is True and s["expiry_days"] < 0


def test_disk_free_and_bad_path(tmp_path):
    assert free_bytes(str(tmp_path)) > 0
    assert free_bytes(str(tmp_path / "does" / "not" / "exist")) is None


def add_done(store, size, tracks):
    i = store.add_item("u", "o", "album", str(size))
    store.update_item(i, status="done", size_bytes=size, track_n=tracks)


def test_forecast_uses_history_then_default(store):
    assert avg_track_bytes(store) == DEFAULT_TRACK_BYTES
    add_done(store, 100_000_000, 10)
    assert avg_track_bytes(store) == 10_000_000
    q = store.add_item("u", "o", "album", "9", expected_tracks=12)
    assert estimate_bytes(store, store.get_item(q)) == 120_000_000
    assert queue_bytes(store) == 120_000_000


def test_forecast_unknown_track_count(store):
    q = store.add_item("u", "o", "album", "9")
    assert estimate_bytes(store, store.get_item(q)) == 10 * DEFAULT_TRACK_BYTES
```

- [ ] **Step 2: Run, expect FAIL**

- [ ] **Step 3: Implement**

`app/cookies.py`:
```python
import os
import time


def cookie_status(path: str, now: float | None = None) -> dict:
    """Existence, file age and earliest apple.com expiry. Cookie names/values are never kept."""
    now = time.time() if now is None else now
    try:
        mtime = os.stat(path).st_mtime
    except OSError:
        return {"exists": False, "age_days": None, "expiry_days": None, "expired": False}
    earliest = None
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.rstrip("\n")
                if line.startswith("#HttpOnly_"):
                    line = line[len("#HttpOnly_"):]
                elif line.startswith("#") or not line.strip():
                    continue
                fields = line.split("\t")
                if len(fields) < 7 or not fields[0].lstrip(".").endswith("apple.com"):
                    continue
                try:
                    exp = int(fields[4])
                except ValueError:
                    continue
                if exp > 0 and (earliest is None or exp < earliest):
                    earliest = exp
    except OSError:
        pass
    expiry_days = None if earliest is None else int((earliest - now) // 86400)
    return {
        "exists": True,
        "age_days": max(0.0, (now - mtime) / 86400),
        "expiry_days": expiry_days,
        "expired": earliest is not None and earliest <= now,
    }
```

`app/disk.py`:
```python
import shutil


def free_bytes(path: str):
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None
```

`app/forecast.py`:
```python
DEFAULT_TRACK_BYTES = 9_000_000
_UNKNOWN_TRACKS = 10


def avg_track_bytes(store) -> int:
    total_bytes, total_tracks = store.history_totals()
    if total_bytes > 0 and total_tracks > 0:
        return total_bytes // total_tracks
    return DEFAULT_TRACK_BYTES


def estimate_bytes(store, item: dict) -> int:
    tracks = item.get("expected_tracks") or item.get("track_n") or _UNKNOWN_TRACKS
    return tracks * avg_track_bytes(store)


def queue_bytes(store) -> int:
    return sum(estimate_bytes(store, i) for i in store.list_items() if i["status"] == "queued")
```

- [ ] **Step 4: Run tests, expect PASS; commit**
```bash
.venv/Scripts/python -m pytest tests/test_status_helpers.py -v
git add -A && git commit -m "feat: cookie expiry, disk and size forecast helpers"
```

---

### Task 7: Catalog fallback and staging match (read-only)

**Files:**
- Create: `app/catalog.py`, `tests/test_catalog.py`

**Interfaces:**
- Produces:
  - `norm(s:str)->str`, `artist_variants(s:str)->set[str]`.
  - `@dataclass Match(level:str, paths:list[str], tracks:int, lossless:bool, note:str="")` with `level in {"exact","likely","none","unavailable"}`.
  - `class Catalog(path:str)`: `match(artist:str, album:str) -> Match`; `mtime() -> float|None`. Loads all rows once and reloads only when the file mtime changes. Never raises: any `sqlite3.Error`/`OSError` returns `Match("unavailable", ...)`.
  - `staging_match(root:str, artist:str, album:str) -> Match` (directory-name scan two levels deep, same normalization).

- [ ] **Step 1: Write the failing tests** `tests/test_catalog.py`
```python
import sqlite3

from app.catalog import Catalog, artist_variants, norm, staging_match


def make_catalog(path, rows):
    db = sqlite3.connect(path)
    db.execute("""CREATE TABLE tracks(id INTEGER PRIMARY KEY AUTOINCREMENT, relative_path TEXT UNIQUE,
                  filename TEXT, category TEXT, format TEXT, size_bytes INTEGER, is_lossless INTEGER)""")
    for rp, lossless in rows:
        db.execute("INSERT INTO tracks(relative_path,filename,category,format,size_bytes,is_lossless) VALUES(?,?,?,?,?,?)",
                   (rp, rp.split("/")[-1], rp.split("/")[1], "FLAC", 1, lossless))
    db.commit()
    db.close()


ROWS = [
    ("Lossless/J-Pop/Ryokuoushoku Shakai (緑黄色社会) ~/Party!!/01. A.flac", 1),
    ("Lossless/J-Pop/Ryokuoushoku Shakai (緑黄色社会) ~/Party!!/02. B.flac", 1),
    ("Lossless/J-Pop/Various Artists ~/+A -PLUS A-/03 溜息の色.flac", 1),
    ("Lossy/J-Pop/Someone ~/Old Album/01 x.mp3", 0),
]


def test_norm_and_variants():
    assert norm("Party!!") == "party"
    assert norm("ＡＢＣ　Ｄ") == "abc d"
    assert artist_variants("Ryokuoushoku Shakai (緑黄色社会) ~") >= {"ryokuoushoku shakai", "緑黄色社会"}


def test_exact_match_by_kanji_artist(tmp_path):
    p = str(tmp_path / "c.sqlite"); make_catalog(p, ROWS)
    m = Catalog(p).match("緑黄色社会", "Party!!")
    assert m.level == "exact" and m.tracks == 2 and m.lossless is True
    assert m.paths == ["Lossless/J-Pop/Ryokuoushoku Shakai (緑黄色社会) ~/Party!!"]


def test_single_suffix_ignored_and_likely_on_other_artist(tmp_path):
    p = str(tmp_path / "c.sqlite"); make_catalog(p, ROWS)
    m = Catalog(p).match("Someone Else", "Party!! - Single")
    assert m.level == "likely"


def test_lossy_only_is_not_lossless(tmp_path):
    p = str(tmp_path / "c.sqlite"); make_catalog(p, ROWS)
    m = Catalog(p).match("Someone", "Old Album")
    assert m.level == "exact" and m.lossless is False


def test_none(tmp_path):
    p = str(tmp_path / "c.sqlite"); make_catalog(p, ROWS)
    assert Catalog(p).match("Nobody", "Nothing Here").level == "none"


def test_missing_file_is_unavailable_not_error(tmp_path):
    m = Catalog(str(tmp_path / "missing.sqlite")).match("a", "b")
    assert m.level == "unavailable"


def test_corrupt_file_is_unavailable(tmp_path):
    p = tmp_path / "bad.sqlite"; p.write_bytes(b"not a database at all")
    assert Catalog(str(p)).match("a", "b").level == "unavailable"


def test_reload_when_file_changes(tmp_path):
    import os
    p = str(tmp_path / "c.sqlite"); make_catalog(p, ROWS[:1])
    c = Catalog(p)
    assert c.match("x", "Old Album").level == "none"
    db = sqlite3.connect(p)
    db.execute("INSERT INTO tracks(relative_path,filename,category,format,size_bytes,is_lossless) VALUES(?,?,?,?,?,?)",
               ("Lossy/J-Pop/Someone ~/Old Album/01 x.mp3", "01 x.mp3", "J-Pop", "MP3", 1, 0))
    db.commit(); db.close()
    st = os.stat(p); os.utime(p, (st.st_atime, st.st_mtime + 5))
    assert c.match("Someone", "Old Album").level == "exact"


def test_staging_match(tmp_path):
    (tmp_path / "緑黄色社会" / "Party!!").mkdir(parents=True)
    (tmp_path / ".tmp").mkdir()
    assert staging_match(str(tmp_path), "緑黄色社会", "Party!!").level == "exact"
    assert staging_match(str(tmp_path), "x", "Nope").level == "none"
    assert staging_match(str(tmp_path / "gone"), "x", "y").level == "none"
```

- [ ] **Step 2: Run, expect FAIL**

- [ ] **Step 3: Implement** `app/catalog.py`
```python
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
```

- [ ] **Step 4: Run tests, expect PASS; commit**
```bash
.venv/Scripts/python -m pytest tests/test_catalog.py -v
git add -A && git commit -m "feat: read-only catalog and staging duplicate match"
```

---

### Task 7b: Library match scoring from `metadata.csv` (read-only)

**Files:**
- Create: `app/library.py`, `tests/test_library.py`

**Interfaces:**
- Consumes: `app.catalog.artist_variants(s:str) -> set[str]` (Task 7).
- Produces:
  - `norm_title(s:str) -> str` (NFKC, casefold, drop `[...]` tags and a trailing `- Single`/`- EP`, strip punctuation, collapse spaces).
  - `is_lossless_codec(codec:str) -> bool` (true when the codec string contains flac, alac, wav, wave, pcm, aiff, ape, wavpack or tta; `audio/mpeg`, `audio/mp4` and unknown are lossy).
  - `@dataclass RemoteAlbum(title:str, artist:str, tracks:int|None=None, track_titles:list=[], durations:list=[])` (durations in seconds, `None` for unknown).
  - `@dataclass LibraryMatch(status:str, confidence:float, album:str, path:str, lossless:bool|None, reasons:list)`; `status` in `in_library_lossless, in_library_lossy, similar, new, unknown, unavailable`.
  - `class Library(path:str)`: `match(remote:RemoteAlbum, exact:float=0.85, similar:float=0.55) -> LibraryMatch`; `mtime()->float|None`. Never raises: OS/CSV/decode errors give `status="unavailable"`; an empty remote (no title and no track titles) gives `unknown`.
- Scoring (spec section 8.4): title 0.35, duration 0.25, overlap 0.25, count 0.15, artist bonus +0.05 (cap 1.0), renormalized over available components, capped at 0.80 when fewer than 3 components exist. Among records within 0.05 of the best score, a lossless record wins.

- [ ] **Step 1: Write the failing tests** `tests/test_library.py`
```python
import csv
import os

from app.library import Library, RemoteAlbum, is_lossless_codec, norm_title

HEADER = ["Title", "Artist", "Album", "Album Artist", "Track Number", "Total Tracks", "Codec", "Duration", "Path"]


def write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        w.writerows(rows)


def album_rows(album, artist, titles, durs, codec, folder):
    return [[t, artist, album, artist, i + 1, len(titles), codec, d, f"E:/Music\\{folder}\\{i + 1:02d} {t}.x"]
            for i, (t, d) in enumerate(zip(titles, durs))]


TITLES = ["心の奥", "草々不一", "溜息"]
DURS = [200.0, 180.5, 240.0]


def remote(**kw):
    base = dict(title="溜息", artist="ロクデナシ", tracks=3, track_titles=TITLES, durations=DURS)
    base.update(kw)
    return RemoteAlbum(**base)


def lib(tmp_path, *groups):
    p = str(tmp_path / "metadata.csv")
    write_csv(p, [r for g in groups for r in g])
    return Library(p)


def test_norm_title_and_codec():
    assert norm_title("Party!! - Single") == "party"
    assert norm_title("Song [WEB-FLAC 24bit／44.1kHz]") == "song"
    assert norm_title("ＡＢＣ　Ｄ") == "abc d"
    assert is_lossless_codec("audio/flac") and is_lossless_codec("audio/x-wav") and is_lossless_codec("alac")
    assert not is_lossless_codec("audio/mpeg") and not is_lossless_codec("")


def test_lossless_copy_is_in_library_lossless(tmp_path):
    L = lib(tmp_path, album_rows("溜息", "Rokudenashi (ロクデナシ) ~", TITLES, DURS, "audio/flac", "Lossless\\J-Pop\\A\\溜息"))
    m = L.match(remote())
    assert m.status == "in_library_lossless" and m.confidence >= 0.85 and m.lossless is True
    assert m.album == "溜息"


def test_lossy_only_copy_is_flagged_lossy(tmp_path):
    L = lib(tmp_path, album_rows("溜息", "x", TITLES, DURS, "audio/mpeg", "Lossy\\A\\溜息"))
    assert L.match(remote()).status == "in_library_lossy"


def test_lossless_wins_over_near_duplicate_lossy(tmp_path):
    L = lib(tmp_path,
            album_rows("溜息", "x", TITLES, DURS, "audio/mpeg", "Lossy\\A\\溜息"),
            album_rows("溜息", "x", TITLES, DURS, "audio/flac", "Lossless\\A\\溜息"))
    assert L.match(remote()).status == "in_library_lossless"


def test_title_only_evidence_can_never_be_in_library(tmp_path):
    L = lib(tmp_path, album_rows("溜息", "x", TITLES, DURS, "audio/flac", "Lossless\\A\\溜息"))
    m = L.match(RemoteAlbum(title="溜息", artist=""))
    assert m.status == "similar" and m.confidence <= 0.80


def test_different_album_is_new(tmp_path):
    L = lib(tmp_path, album_rows("Other", "x", ["a", "b"], [100.0, 90.0], "audio/flac", "Lossless\\A\\Other"))
    assert L.match(remote()).status == "new"


def test_renamed_album_with_same_tracks_is_similar(tmp_path):
    L = lib(tmp_path, album_rows("Tameiki (Romaji title)", "x", TITLES, DURS, "audio/flac", "Lossless\\A\\Tameiki"))
    m = L.match(remote())
    assert m.status == "similar", m


def test_duration_mismatch_lowers_score(tmp_path):
    off = lib(tmp_path, album_rows("溜息", "x", TITLES, [100.0, 100.0, 100.0], "audio/flac", "Lossless\\A\\溜息"))
    conf_off = off.match(remote()).confidence
    same = lib(tmp_path, album_rows("溜息", "x", TITLES, DURS, "audio/flac", "Lossless\\A\\溜息"))
    assert conf_off < same.match(remote()).confidence


def test_blank_remote_durations_are_ignored_not_fatal(tmp_path):
    L = lib(tmp_path, album_rows("溜息", "x", TITLES, DURS, "audio/flac", "Lossless\\A\\溜息"))
    m = L.match(remote(durations=[None, None, None]))
    assert m.status in ("in_library_lossless", "similar") and m.confidence > 0.5


def test_artist_bonus_from_kanji_variant(tmp_path):
    rows = album_rows("溜息", "Rokudenashi (ロクデナシ) ~", TITLES, [100.0, 100.0, 100.0], "audio/flac", "L\\A\\溜息")
    with_artist = lib(tmp_path, rows).match(remote(artist="ロクデナシ")).confidence
    without = lib(tmp_path, rows).match(remote(artist="Someone Else")).confidence
    assert with_artist > without


def test_unknown_when_nothing_to_compare(tmp_path):
    L = lib(tmp_path, album_rows("a", "x", ["t"], [1.0], "audio/flac", "L\\A\\a"))
    assert L.match(RemoteAlbum(title="", artist="")).status == "unknown"


def test_missing_and_broken_files_are_unavailable(tmp_path):
    assert Library(str(tmp_path / "none.csv")).match(remote()).status == "unavailable"
    bad = tmp_path / "bad.csv"
    bad.write_bytes(b"\x80\x81\x82\xff\xfe")
    assert Library(str(bad)).match(remote()).status == "unavailable"


def test_reload_when_file_changes(tmp_path):
    p = str(tmp_path / "m.csv")
    write_csv(p, [])
    L = Library(p)
    assert L.match(remote()).status == "new"
    write_csv(p, album_rows("溜息", "x", TITLES, DURS, "audio/flac", "L\\A\\溜息"))
    st = os.stat(p)
    os.utime(p, (st.st_atime, st.st_mtime + 5))
    assert L.match(remote()).status == "in_library_lossless"


def test_bad_duration_cells_do_not_crash(tmp_path):
    rows = album_rows("溜息", "x", TITLES, ["", "abc", "240"], "audio/flac", "L\\A\\溜息")
    assert lib(tmp_path, rows).match(remote()).status in ("similar", "in_library_lossless", "new")
```

- [ ] **Step 2: Run, expect FAIL** (`ModuleNotFoundError: app.library`)

Run: `.venv/Scripts/python -m pytest tests/test_library.py -v`

- [ ] **Step 3: Implement** `app/library.py`
```python
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
        if r.artist and (artist_variants(r.artist) & rec.artists):
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
```

- [ ] **Step 4: Run tests, expect PASS; commit**
```bash
.venv/Scripts/python -m pytest tests/test_library.py -v
git add -A && git commit -m "feat: fuzzy library match over metadata.csv with confidence"
```

---

### Task 8: Post-download checker

**Files:**
- Create: `app/checker.py`, `app/checker_rules.json`, `tests/test_checker.py`

**Interfaces:**
- Produces:
  - `load_rules(path:Path|None=None) -> dict`.
  - `find_output_dirs(staging:str, since:float) -> list[Path]` (album dirs `<staging>/<artist>/<album>` whose mtime >= `since - 2`; ignores dot-dirs).
  - `check_album(album:Path, expected_tracks:int|None, rules:dict|None=None) -> list[str]` (human-readable findings, empty when clean).
  - `dir_size(paths:list[Path]) -> int`.

- [ ] **Step 1: Write the failing tests** `tests/test_checker.py`
```python
import os
import time

from app.checker import check_album, dir_size, find_output_dirs, load_rules


def album(tmp_path, artist="Artist ~", name="Album", files=("01 A.m4a", "01 A.lrc", "Cover.jpg")):
    d = tmp_path / artist / name
    d.mkdir(parents=True)
    for f in files:
        (d / f).write_bytes(b"x")
    return d


def test_clean_album(tmp_path):
    assert check_album(album(tmp_path), 1) == []


def test_missing_cover_lrc_and_count(tmp_path):
    d = album(tmp_path, files=("01 A.m4a", "02 B.m4a"))
    f = check_album(d, 3)
    assert any("Cover.jpg" in x for x in f)
    assert sum("lyrics" in x for x in f) == 2
    assert any("2 audio files, expected 3" in x for x in f)


def test_zero_byte_and_no_audio(tmp_path):
    d = album(tmp_path, files=("Cover.jpg",))
    (d / "01 A.m4a").write_bytes(b"")
    assert any("zero-byte" in x for x in check_album(d, None))
    d2 = album(tmp_path, name="Empty", files=("Cover.jpg",))
    assert any("no audio" in x for x in check_album(d2, None))


def test_artist_naming_rules(tmp_path):
    d = album(tmp_path, artist="緑黄色社会")
    f = check_album(d, 1)
    assert any("' ~'" in x for x in f)
    assert any("Romaji" in x for x in f)
    d2 = album(tmp_path, artist="Ryokuoushoku Shakai (緑黄色社会) ~", name="B")
    assert not any("artist folder" in x for x in check_album(d2, 1))


def test_forbidden_chars(tmp_path):
    d = album(tmp_path, files=("01 A?.m4a", "01 A?.lrc", "Cover.jpg"))
    assert any("forbidden character" in x for x in check_album(d, 1))


def test_find_output_dirs_by_mtime(tmp_path):
    old = album(tmp_path, artist="Old ~", name="Old")
    os.utime(old, (1000, 1000))
    new = album(tmp_path, artist="New ~", name="New")
    (tmp_path / ".tmp").mkdir()
    (tmp_path / ".tmp" / "junk").mkdir()
    found = find_output_dirs(str(tmp_path), since=time.time() - 60)
    assert found == [new]
    assert find_output_dirs(str(tmp_path / "gone"), 0) == []


def test_dir_size(tmp_path):
    d = album(tmp_path)
    assert dir_size([d]) == 3


def test_rules_file_loads():
    r = load_rules()
    assert r["cover_name"] == "Cover.jpg" and ".m4a" in r["audio_ext"]
```

- [ ] **Step 2: Run, expect FAIL**

- [ ] **Step 3: Implement**

`app/checker_rules.json`:
```json
{
  "cover_name": "Cover.jpg",
  "audio_ext": [".m4a"],
  "require_lrc": true,
  "artist_suffix": " ~",
  "require_romaji_kanji": true,
  "forbidden_chars": "<>:\"/\\|?*"
}
```

`app/checker.py`:
```python
import json
import os
from pathlib import Path

RULES_PATH = Path(__file__).with_name("checker_rules.json")


def load_rules(path: Path | None = None) -> dict:
    return json.loads((path or RULES_PATH).read_text(encoding="utf-8"))


def find_output_dirs(staging: str, since: float) -> list:
    out = []
    try:
        for artist in sorted(os.scandir(staging), key=lambda e: e.name):
            if artist.name.startswith(".") or not artist.is_dir():
                continue
            for album in sorted(os.scandir(artist.path), key=lambda e: e.name):
                if album.is_dir() and album.stat().st_mtime >= since - 2:
                    out.append(Path(album.path))
    except OSError:
        return []
    return out


def dir_size(paths: list) -> int:
    total = 0
    for p in paths:
        for f in Path(p).rglob("*"):
            if f.is_file():
                total += f.stat().st_size
    return total


def check_album(album: Path, expected_tracks, rules: dict | None = None) -> list:
    rules = rules or load_rules()
    findings = []
    files = [f for f in Path(album).iterdir() if f.is_file()]
    names = {f.name.lower() for f in files}
    audio = [f for f in files if f.suffix.lower() in rules["audio_ext"]]
    if not audio:
        findings.append("no audio files")
    for f in files:
        if f.stat().st_size == 0:
            findings.append(f"zero-byte file: {f.name}")
    if rules["cover_name"].lower() not in names:
        findings.append(f"missing {rules['cover_name']}")
    if rules.get("require_lrc"):
        for f in audio:
            if f.with_suffix(".lrc").name.lower() not in names:
                findings.append(f"missing lyrics for {f.name}")
    if expected_tracks and audio and len(audio) != expected_tracks:
        findings.append(f"{len(audio)} audio files, expected {expected_tracks}")
    bad = rules["forbidden_chars"]
    for name in [Path(album).name, *[f.name for f in files]]:
        hit = [c for c in bad if c in name]
        if hit:
            findings.append(f"forbidden character {hit[0]!r} in {name}")
    artist = Path(album).parent.name
    suffix = rules.get("artist_suffix")
    if suffix and not artist.endswith(suffix):
        findings.append(f"artist folder has no '{suffix}' suffix (naming for manual move)")
    if rules.get("require_romaji_kanji") and not artist.isascii() and "(" not in artist:
        findings.append("artist folder lacks 'Romaji (Kanji)' form (naming for manual move)")
    return findings
```

- [ ] **Step 4: Run tests, expect PASS; commit**
```bash
.venv/Scripts/python -m pytest tests/test_checker.py -v
git add -A && git commit -m "feat: post-download folder checker"
```

---

### Task 8b: Codec detection with ffprobe

**Files:**
- Modify: `app/checker.py`
- Create: `tests/test_codec.py`

**Interfaces:**
- Produces (added to `app.checker`):
  - `detect_codec(path, run=subprocess.run) -> dict|None` returning `{"codec": str, "kbps": int|None}`; `None` when ffprobe is missing, exits non-zero, times out (30 s) or returns nothing.
  - `classify_album(results:list) -> tuple[str, str, list]` = `(codec_label, classification, findings)`. Labels: `"aac 256k"`, `"alac"`, `"unknown"`. Classification: `"Lossy/ [AAC 256k]"` (bitrate = median rounded to the nearest 32 kbps, or `"Lossy/ [AAC]"` if unknown), `"valid for Lossless/"` for alac/flac, else an explanatory string.
  - `probe_album(album:Path, rules:dict|None=None, run=subprocess.run) -> tuple[str, str, list]`.
- The codec is read from ffprobe, never from the file extension.

- [ ] **Step 1: Write the failing tests** `tests/test_codec.py`
```python
import json
import subprocess
from types import SimpleNamespace

from app.checker import classify_album, detect_codec, probe_album


def fake_run(payload, code=0):
    def run(cmd, **kw):
        assert cmd[0] == "ffprobe" and "-select_streams" in cmd
        return SimpleNamespace(returncode=code, stdout=json.dumps(payload))
    return run


def test_detect_aac_with_bitrate():
    r = detect_codec("x.m4a", fake_run({"streams": [{"codec_name": "aac", "bit_rate": "256000"}]}))
    assert r == {"codec": "aac", "kbps": 256}


def test_detect_alac_without_bitrate():
    r = detect_codec("x.m4a", fake_run({"streams": [{"codec_name": "alac"}]}))
    assert r == {"codec": "alac", "kbps": None}


def test_detect_failures_return_none():
    assert detect_codec("x", fake_run({"streams": []})) is None
    assert detect_codec("x", fake_run({}, code=1)) is None

    def missing(cmd, **kw):
        raise FileNotFoundError("ffprobe")

    def slow(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 30)

    assert detect_codec("x", missing) is None
    assert detect_codec("x", slow) is None


def test_classify_aac_rounds_to_32():
    label, cls, findings = classify_album([{"codec": "aac", "kbps": 255}, {"codec": "aac", "kbps": 258}])
    assert (label, cls, findings) == ("aac 256k", "Lossy/ [AAC 256k]", [])


def test_classify_alac_is_lossless_valid():
    label, cls, _ = classify_album([{"codec": "alac", "kbps": 900}])
    assert label == "alac" and cls == "valid for Lossless/"


def test_classify_mixed_and_partial_failures():
    label, cls, findings = classify_album([{"codec": "aac", "kbps": 256}, {"codec": "alac", "kbps": None}, None])
    assert any("mixed codecs" in f for f in findings)
    assert any("ffprobe failed on 1 file" in f for f in findings)


def test_classify_all_unknown():
    label, cls, findings = classify_album([None, None])
    assert label == "unknown" and findings and "ffprobe" in findings[0]


def test_extension_is_not_trusted(tmp_path):
    d = tmp_path / "A" / "B"
    d.mkdir(parents=True)
    (d / "01 x.m4a").write_bytes(b"x")
    label, cls, _ = probe_album(d, run=fake_run({"streams": [{"codec_name": "alac"}]}))
    assert label == "alac"
    label, cls, _ = probe_album(d, run=fake_run({"streams": [{"codec_name": "aac", "bit_rate": "128000"}]}))
    assert cls == "Lossy/ [AAC 128k]"
```

- [ ] **Step 2: Run, expect FAIL** (`ImportError: cannot import name 'detect_codec'`)

- [ ] **Step 3: Implement** (append to `app/checker.py`; add `import statistics`, `import subprocess` and `from collections import Counter` to its imports)
```python
def detect_codec(path, run=subprocess.run):
    cmd = ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
           "stream=codec_name,bit_rate", "-of", "json", str(path)]
    try:
        r = run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            return None
        streams = json.loads(r.stdout).get("streams") or []
        if not streams or not streams[0].get("codec_name"):
            return None
        br = str(streams[0].get("bit_rate", ""))
        return {"codec": streams[0]["codec_name"], "kbps": round(int(br) / 1000) if br.isdigit() else None}
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def classify_album(results: list) -> tuple:
    known = [r for r in results if r]
    findings = []
    if not known:
        return "unknown", "unknown codec (ffprobe unavailable or failed)", ["codec unknown: ffprobe unavailable or failed"]
    failed = len(results) - len(known)
    if failed:
        findings.append(f"ffprobe failed on {failed} file(s)")
    codecs = Counter(r["codec"] for r in known)
    if len(codecs) > 1:
        findings.append("mixed codecs: " + ", ".join(sorted(codecs)))
    dominant = codecs.most_common(1)[0][0]
    if dominant == "aac":
        kbps = [r["kbps"] for r in known if r["codec"] == "aac" and r["kbps"]]
        if kbps:
            n = int(round(statistics.median(kbps) / 32) * 32)
            return f"aac {n}k", f"Lossy/ [AAC {n}k]", findings
        return "aac", "Lossy/ [AAC]", findings
    if dominant in ("alac", "flac"):
        return dominant, "valid for Lossless/", findings
    return dominant, f"unrecognized codec {dominant}", findings


def probe_album(album, rules=None, run=subprocess.run) -> tuple:
    rules = rules or load_rules()
    files = sorted(f for f in Path(album).iterdir() if f.is_file() and f.suffix.lower() in rules["audio_ext"])
    return classify_album([detect_codec(f, run) for f in files])
```

- [ ] **Step 4: Run tests, expect PASS; commit**
```bash
.venv/Scripts/python -m pytest tests/test_codec.py tests/test_checker.py -v
git add -A && git commit -m "feat: detect codec with ffprobe and classify albums"
```

---

### Task 9: Preview service (public page metadata)

**Files:**
- Create: `app/preview.py`, `tests/test_preview.py`

**Interfaces:**
- Consumes: `app.urls.ParsedUrl`.
- Produces:
  - `@dataclass Preview(title:str, artist:str, tracks:int|None, year:str|None, source:str, track_titles:list=[], durations:list=[])` with `source in {"web","none"}`; `durations` in seconds (`None` per unknown track). Method `remote() -> app.library.RemoteAlbum` (used by the library check).
  - `parse_iso_duration(s:str|None) -> float|None` (`"PT3M25S"` -> 205.0, `"PT1H2M3.5S"` -> 3723.5, junk -> `None`).
  - The default getter sends `Accept-Language: ja-JP,ja;q=0.9` so names match what gamdl downloads from `/jp/`.
  - `parse_page(html:str) -> Preview|None`: reads `application/ld+json` (`MusicAlbum`/`MusicPlaylist`), falls back to `og:title`.
  - `class PreviewService(getter=None, delay=(3.0, 8.0), sleep=asyncio.sleep, rand=random.uniform)`; `async fetch(parsed) -> Preview`. Requests are serialized by a lock and separated by a random delay (none before the first). It sends no cookies. Any error returns `Preview("", "", None, None, "none")`.
  - Default `getter(url) -> str` uses `httpx.AsyncClient(timeout=15, follow_redirects=True)` with a plain browser User-Agent and no cookies.
- NOTE: the JSON-LD structure below is an assumption from the schema.org vocabulary; Task 13 has a manual verification step against one real public page (with user approval).

- [ ] **Step 1: Write the failing tests** `tests/test_preview.py`
```python
import json

from app.preview import Preview, PreviewService, parse_iso_duration, parse_page
from app.urls import normalize

LD = {
    "@context": "http://schema.org", "@type": "MusicAlbum", "name": "溜息",
    "byArtist": [{"@type": "MusicGroup", "name": "アーティスト"}],
    "numTracks": 5, "datePublished": "2025-03-12",
}
HTML = f'<html><head><script type="application/ld+json">{json.dumps(LD, ensure_ascii=False)}</script></head></html>'


def test_parse_jsonld():
    p = parse_page(HTML)
    assert (p.title, p.artist, p.tracks, p.year, p.source) == ("溜息", "アーティスト", 5, "2025", "web")


def test_parse_jsonld_counts_track_list():
    ld = dict(LD); del ld["numTracks"]; ld["track"] = [{"name": "a"}, {"name": "b"}]
    p = parse_page(f'<script type="application/ld+json">{json.dumps(ld)}</script>')
    assert p.tracks == 2


def test_parse_iso_duration():
    assert parse_iso_duration("PT3M25S") == 205.0
    assert parse_iso_duration("PT1H2M3.5S") == 3723.5
    assert parse_iso_duration("PT45S") == 45.0
    assert parse_iso_duration("junk") is None and parse_iso_duration(None) is None


def test_parse_track_titles_and_durations_into_remote_album():
    ld = dict(LD); del ld["numTracks"]
    ld["track"] = [{"@type": "MusicRecording", "name": "心の奥", "duration": "PT3M20S"},
                   {"@type": "MusicRecording", "name": "草々不一"}]
    p = parse_page(f'<script type="application/ld+json">{json.dumps(ld, ensure_ascii=False)}</script>')
    assert p.track_titles == ["心の奥", "草々不一"] and p.durations == [200.0, None] and p.tracks == 2
    r = p.remote()
    assert (r.title, r.artist, r.tracks, r.track_titles, r.durations) == ("溜息", "アーティスト", 2, ["心の奥", "草々不一"], [200.0, None])


def test_parse_og_fallback():
    p = parse_page('<meta property="og:title" content="Frozen Flower - Single by X on Apple Music">')
    assert p.title == "Frozen Flower - Single by X" and p.source == "web"


def test_parse_nothing_returns_none():
    assert parse_page("<html></html>") is None
    assert parse_page('<script type="application/ld+json">{broken</script>') is None


async def test_fetch_serializes_with_delay_and_survives_errors():
    calls, sleeps = [], []

    async def getter(url):
        calls.append(url)
        if "fail" in url:
            raise RuntimeError("boom")
        return HTML

    async def sleep(s): sleeps.append(s)

    svc = PreviewService(getter=getter, sleep=sleep, rand=lambda a, b: a)
    a = normalize("https://music.apple.com/jp/album/1")
    b = normalize("https://music.apple.com/jp/album/2")
    r1 = await svc.fetch(a)
    r2 = await svc.fetch(b)
    assert r1.title == "溜息" and sleeps == [3.0]
    assert calls == [a.normalized, b.normalized]

    async def failing(url): raise RuntimeError("boom")
    svc2 = PreviewService(getter=failing, sleep=sleep, rand=lambda a, b: a)
    r = await svc2.fetch(a)
    assert r == Preview("", "", None, None, "none")
```

- [ ] **Step 2: Run, expect FAIL**

- [ ] **Step 3: Implement** `app/preview.py`
```python
import asyncio
import html as htmllib
import json
import random
import re
from dataclasses import dataclass, field

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
    m = _ISO.match(s or "")
    if not m or not any(m.groups()):
        return None
    h, mi, sec = m.groups()
    return int(h or 0) * 3600 + int(mi or 0) * 60 + float(sec or 0)


def _artist(by) -> str:
    if isinstance(by, list):
        by = by[0] if by else {}
    return by.get("name", "") if isinstance(by, dict) else str(by or "")


def parse_page(page: str):
    for raw in _LD.findall(page):
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        for d in data if isinstance(data, list) else [data]:
            if isinstance(d, dict) and d.get("@type") in ("MusicAlbum", "MusicPlaylist"):
                listed = [t for t in d.get("track", []) if isinstance(t, dict)] if isinstance(d.get("track"), list) else []
                tracks = d.get("numTracks")
                if tracks is None and listed:
                    tracks = len(listed)
                year = str(d.get("datePublished", ""))[:4] or None
                return Preview(d.get("name", ""), _artist(d.get("byArtist")), tracks, year, "web",
                               [t.get("name", "") for t in listed], [parse_iso_duration(t.get("duration")) for t in listed])
    m = _OG.search(page)
    if m:
        title = htmllib.unescape(m[1]).removesuffix(" on Apple Music").strip()
        return Preview(title, "", None, None, "web")
    return None


async def _default_getter(url: str) -> str:
    import httpx
    headers = {"User-Agent": _UA, "Accept-Language": "ja-JP,ja;q=0.9"}
    async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers=headers) as c:
        r = await c.get(url)
        r.raise_for_status()
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
```

- [ ] **Step 4: Run tests, expect PASS; commit**
```bash
.venv/Scripts/python -m pytest tests/test_preview.py -v
git add -A && git commit -m "feat: preview service from public album pages"
```

---

### Task 10: Event bus, fake gamdl, and the sequential runner

**Files:**
- Create: `app/bus.py`, `tools/fake_gamdl_safe.py`, `app/runner.py`, `tests/test_bus.py`, `tests/test_runner.py`

**Interfaces:**
- Consumes: `Store` (Task 5), `Config` (Task 1), `parse_line/LineSplitter/events` (Task 3), `Guard` (Task 4), `disk.free_bytes`, `forecast.estimate_bytes`, `checker.find_output_dirs/check_album/dir_size` (Tasks 6, 8), `settings.parse_range`.
- Produces:
  - `EventBus`: `subscribe() -> asyncio.Queue`, `unsubscribe(q)`, `publish(event:dict)` (non-blocking; drops for full queues).
  - `Runner(store, bus, cfg, sleep=asyncio.sleep, clock=time.time, rand=random.uniform)` with:
    attributes `current_id:int|None`, `live:dict` (keys `track_pct, speed, delay_kind, delay_until`), `guard:Guard`;
    `async step() -> bool` (one scheduling decision; returns True if an item ran);
    `async run_forever()`, `stop()`, `wake()`, `async pause()`, `async resume()`, `async cancel(item_id)`.
  - Store flags used: `paused` (`"1"`), banner kinds `rate_limited, cookies, cap_reached, disk_low, forecast, busy, gamdl_missing, recovered`.
  - Bus events published: `{"type":"state"}`, `{"type":"progress","item_id","pct","speed","delay_kind","delay_until"}`, `{"type":"log","item_id","level","text","ts"}`.
- Behavior contract: see spec section 3 "runner". Decisions worth restating: pause = stop after the current track (terminate at the next `TrackDelay` event) and return the item to `queued`; rate-limit or auth verdict terminates immediately, returns the item to `queued`, sets `paused` and the banner; a lock-held message (`already running`) returns the item to `queued`, sets banner `busy` and pauses; between items the runner waits `rand(album_delay)` minus time elapsed since the last item finished (`_last_finish`), in 1 s slices, aborting if paused. Retry (spec 8.9): when a run ends with track errors or a crash that is neither a guard verdict nor a cancel/pause, and `attempts < track_retries`, the item returns to `queued` with `attempts+1` and `not_before = now + rand(retry_backoff)`; `step()` only considers items whose `not_before` has passed (`store.next_queued(clock())`), so other due items run meanwhile, still one at a time. `_finish` is `async` and runs the blocking ffprobe/checker work via `asyncio.to_thread`; on success it stores `codec`, `classification`, `findings`, `output_path`, `size_bytes`.

- [ ] **Step 1: Write the bus test and implement the bus**

`tests/test_bus.py`:
```python
import asyncio

from app.bus import EventBus


async def test_publish_reaches_all_and_drops_when_full():
    bus = EventBus()
    a, b = bus.subscribe(), bus.subscribe()
    bus.publish({"type": "state"})
    assert await a.get() == {"type": "state"} and await b.get() == {"type": "state"}
    for _ in range(500):
        bus.publish({"type": "log"})  # must never raise or block
    assert b.qsize() == 100  # bounded: extra events are dropped
    bus.unsubscribe(a)
    before = a.qsize()
    bus.publish({"type": "state"})
    assert a.qsize() == before  # unsubscribed queues receive nothing
```

`app/bus.py`:
```python
import asyncio


class EventBus:
    def __init__(self):
        self._subs: set = set()

    def subscribe(self) -> asyncio.Queue:
        q = asyncio.Queue(maxsize=100)
        self._subs.add(q)
        return q

    def unsubscribe(self, q) -> None:
        self._subs.discard(q)

    def publish(self, event: dict) -> None:
        for q in list(self._subs):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass
```
Run: `.venv/Scripts/python -m pytest tests/test_bus.py -v` → PASS.

- [ ] **Step 2: Write the fake wrapper** `tools/fake_gamdl_safe.py`
```python
#!/usr/bin/env python3
"""Stand-in for gamdl-safe used by tests and local dev. Scenario via FAKE_SCENARIO:
ok | rate_limit | auth | slow | crash | busy | flaky | fail. Never talks to the network.
flaky/fail: track 2 raises a non-rate-limit error ("read timeout"); flaky succeeds on the next
run when FAKE_STATE points at a file path that survives between runs."""
import os
import sys
import time
from pathlib import Path

scenario = os.environ.get("FAKE_SCENARIO", "ok")
nap = float(os.environ.get("FAKE_SLEEP", "0.02"))
tracks = int(os.environ.get("FAKE_TRACKS", "3"))
out = os.environ.get("FAKE_OUT")
url = sys.argv[-1]


def say(s):
    sys.stdout.write(s)
    sys.stdout.flush()


if scenario == "busy":
    say("gamdl-safe already running (lock held) - refusing to run in parallel\n")
    sys.exit(1)

say(f'[INFO     19:00:00] URL   1/1  Processing "{url}"\n')
failed = 0
for i in range(1, tracks + 1):
    say(f'[INFO     19:00:{i:02d}] [Track {i:3d}/{tracks:<3d}] Downloading "Song {i}"\n')
    for pct in ("10.0", "55.5", "100.0"):
        say(f"[download]  {pct}% of ~ 5.00MiB at 1.00MiB/s (frag 1/2)\r")
        time.sleep(nap)
    say("\n")
    if scenario == "rate_limit":
        say(f'[ERROR    19:00:{i:02d}] Error downloading "Song {i}": HTTP 429 Too Many Requests\n')
        continue
    if scenario in ("flaky", "fail") and i == 2:
        # "flaky" fails track 2 only the first time (state file), "fail" fails it every time
        state = os.environ.get("FAKE_STATE")
        if not (scenario == "flaky" and state and Path(state).exists()):
            if state:
                Path(state).write_text("1")
            say('[ERROR    19:00:02] Error downloading "Song 2": read timeout\n')
            failed += 1
            continue
    if scenario == "auth":
        say('[ERROR    19:00:01] Error downloading "Song 1": 401 Unauthorized, invalid cookies\n')
        time.sleep(30)
        continue
    if out:
        d = Path(out) / "Artist ~" / "Album"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{i:02d} Song {i}.m4a").write_bytes(b"x" * 1000)
        (d / f"{i:02d} Song {i}.lrc").write_bytes(b"x")
        (d / "Cover.jpg").write_bytes(b"x")
    say("[gamdl-safe] track delay 1s\n")
    time.sleep(nap)
    if scenario == "slow":
        time.sleep(float(os.environ.get("FAKE_SLOW", "0.5")))
    if scenario == "crash":
        sys.exit(3)
n_err = tracks if scenario == "rate_limit" else failed
say(f"[INFO     19:00:59] Finished with {n_err} error(s)\n")
```

- [ ] **Step 3: Write the failing runner tests** `tests/test_runner.py`
```python
import sys
from pathlib import Path

import pytest

from app.bus import EventBus
from app.config import Config
from app.runner import Runner

FAKE = str(Path(__file__).resolve().parent.parent / "tools" / "fake_gamdl_safe.py")
NOW = 1_000_000.0


@pytest.fixture
def env(tmp_path, monkeypatch, store):
    monkeypatch.setenv("FAKE_OUT", str(tmp_path / "staging"))
    monkeypatch.setenv("FAKE_SLEEP", "0.01")
    cfg = Config(db_path=":memory:", gamdl_cmd=[sys.executable, FAKE], extra_args=[],
                 staging_dir=str(tmp_path / "staging"), disk_path=str(tmp_path), cookies_path="",
                 catalog_path="", host="127.0.0.1", port=0, autostart=False)
    # keep the disk gate and retries out of tests that don't target them
    store.put_settings({"low_disk_gb": 0, "track_retries": 0})
    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)

    runner = Runner(store, EventBus(), cfg, sleep=fake_sleep, clock=lambda: NOW, rand=lambda a, b: a)
    return runner, store, sleeps, monkeypatch


def add(store, n, **kw):
    return store.add_item(f"https://music.apple.com/jp/album/{n}", "o", "album", str(n), **kw)


async def test_success_marks_done_with_output_and_findings(env):
    runner, store, _, _ = env
    a = add(store, 1, expected_tracks=3)
    assert await runner.step() is True
    it = store.get_item(a)
    assert it["status"] == "done" and it["errors"] == 0
    assert it["output_path"].endswith("Album") and it["size_bytes"] >= 3000
    assert it["codec"] == "unknown"  # the fake writes junk bytes, so ffprobe cannot identify them (or is absent)
    assert "Lossy" not in (it["classification"] or "") and "unknown codec" in it["classification"]
    assert it["track_n"] == 3
    assert [t["status"] for t in store.list_tracks(a)] == ["done"] * 3
    assert store.count_tracks_since(0) == 3
    assert runner.current_id is None


async def test_album_delay_between_items_and_never_overlapping(env):
    runner, store, sleeps, _ = env
    a, b = add(store, 1), add(store, 2)
    await runner.step()
    assert sleeps == [] or sum(sleeps) == 0
    await runner.step()
    assert sum(sleeps) == 60  # album_delay lower bound, elapsed time is 0 with the frozen clock
    ia, ib = store.get_item(a), store.get_item(b)
    assert ia["finished_at"] <= ib["started_at"]


async def test_concurrent_steps_are_serialized(env):
    import asyncio
    runner, store, _, mp = env
    mp.setenv("FAKE_SCENARIO", "slow")
    mp.setenv("FAKE_SLOW", "0.2")
    a, b = add(store, 1), add(store, 2)
    await asyncio.gather(runner.step(), runner.step())
    assert store.get_item(a)["status"] == "done" and store.get_item(b)["status"] == "done"


async def test_rate_limit_auto_pauses_and_requeues(env):
    runner, store, _, mp = env
    mp.setenv("FAKE_SCENARIO", "rate_limit")
    mp.setenv("FAKE_TRACKS", "6")
    a = add(store, 1)
    await runner.step()
    assert store.get_item(a)["status"] == "queued"
    assert store.get_flag("paused") == "1"
    assert store.get_banner()["kind"] == "rate_limited"
    assert await runner.step() is False  # paused: nothing runs


async def test_auth_failure_pauses_with_cookie_banner(env):
    runner, store, _, mp = env
    mp.setenv("FAKE_SCENARIO", "auth")
    a = add(store, 1)
    await runner.step()
    assert store.get_banner()["kind"] == "cookies"
    assert store.get_item(a)["status"] == "queued"


async def test_resume_clears_pause_and_banner(env):
    runner, store, _, mp = env
    mp.setenv("FAKE_SCENARIO", "rate_limit")
    mp.setenv("FAKE_TRACKS", "6")
    add(store, 1)
    await runner.step()
    mp.setenv("FAKE_SCENARIO", "ok")
    await runner.resume()
    assert store.get_flag("paused") is None and store.get_banner() is None
    assert await runner.step() is True


async def test_crash_without_finished_is_error(env):
    runner, store, _, mp = env
    mp.setenv("FAKE_SCENARIO", "crash")
    a = add(store, 1)
    await runner.step()
    it = store.get_item(a)
    assert it["status"] == "error" and "exit" in it["error_msg"]


async def test_flaky_track_is_retried_after_backoff_then_succeeds(env, tmp_path):
    runner, store, _, mp = env
    store.put_settings({"track_retries": 2})
    mp.setenv("FAKE_SCENARIO", "flaky")
    mp.setenv("FAKE_STATE", str(tmp_path / "state"))
    a = add(store, 1)
    await runner.step()
    it = store.get_item(a)
    assert it["status"] == "queued" and it["attempts"] == 1 and it["not_before"] > NOW
    assert "retry 1/2" in it["error_msg"]
    assert store.get_flag("paused") is None  # a plain retry never pauses the queue
    assert await runner.step() is False  # still backing off
    runner._clock = lambda: NOW + 10_000
    assert await runner.step() is True
    it = store.get_item(a)
    assert it["status"] == "done" and it["attempts"] == 1 and it["not_before"] is None


async def test_retries_exhausted_becomes_error(env):
    runner, store, _, mp = env
    store.put_settings({"track_retries": 1})
    mp.setenv("FAKE_SCENARIO", "fail")
    a = add(store, 1)
    await runner.step()
    assert store.get_item(a)["status"] == "queued"
    runner._clock = lambda: NOW + 10_000
    await runner.step()
    it = store.get_item(a)
    assert it["status"] == "error" and it["attempts"] == 1 and it["errors"] == 1


async def test_backoff_lets_other_items_run_but_still_one_at_a_time(env, tmp_path):
    runner, store, _, mp = env
    store.put_settings({"track_retries": 1})
    mp.setenv("FAKE_SCENARIO", "flaky")
    mp.setenv("FAKE_STATE", str(tmp_path / "state"))
    a, b = add(store, 1), add(store, 2)
    await runner.step()  # a fails once and backs off
    await runner.step()  # b runs while a waits (state file now exists, so b succeeds)
    assert store.get_item(b)["status"] == "done" and store.get_item(a)["status"] == "queued"


async def test_rate_limit_is_never_retried(env):
    runner, store, _, mp = env
    store.put_settings({"track_retries": 2})
    mp.setenv("FAKE_SCENARIO", "rate_limit")
    mp.setenv("FAKE_TRACKS", "6")
    a = add(store, 1)
    await runner.step()
    it = store.get_item(a)
    assert it["attempts"] == 0 and it["not_before"] is None
    assert store.get_banner()["kind"] == "rate_limited" and store.get_flag("paused") == "1"


async def test_lock_held_requeues_and_shows_busy(env):
    runner, store, _, mp = env
    mp.setenv("FAKE_SCENARIO", "busy")
    a = add(store, 1)
    await runner.step()
    assert store.get_item(a)["status"] == "queued"
    assert store.get_banner()["kind"] == "busy" and store.get_flag("paused") == "1"


async def test_missing_binary_pauses_with_banner(env):
    runner, store, _, _ = env
    runner.cfg.gamdl_cmd = ["/definitely/not/here"]
    a = add(store, 1)
    await runner.step()
    assert store.get_banner()["kind"] == "gamdl_missing"
    assert store.get_item(a)["status"] == "error"


async def test_cap_pauses_before_starting(env):
    runner, store, _, _ = env
    store.put_settings({"max_tracks_per_24h": 2})
    for _ in range(2):
        store.record_track(NOW - 10)
    a = add(store, 1)
    assert await runner.step() is False
    assert store.get_banner()["kind"] == "cap_reached"
    assert store.get_item(a)["status"] == "queued"


async def test_low_disk_pauses_before_starting(env):
    runner, store, _, _ = env
    store.put_settings({"low_disk_gb": 2000})
    a = add(store, 1)
    assert await runner.step() is False
    assert store.get_banner()["kind"] in ("disk_low", "forecast")


async def test_cancel_queued_item_and_cancel_while_paused(env):
    runner, store, _, _ = env
    a = add(store, 1)
    await runner.pause()
    await runner.cancel(a)
    assert store.get_item(a)["status"] == "cancelled"
    assert await runner.step() is False


async def test_pause_while_idle_is_harmless(env):
    runner, store, _, _ = env
    await runner.pause()
    assert store.get_flag("paused") == "1"
    await runner.resume()
    assert await runner.step() is False  # empty queue


async def test_track_delay_and_skip_events_update_store(env):
    runner, store, _, _ = env
    a = add(store, 1)
    await runner.step()
    log = " ".join(r["text"] for r in store.tail_log(50))
    assert "Downloading" in log and "track delay" in log
```

- [ ] **Step 4: Run, expect FAIL** (`ModuleNotFoundError: app.runner`)

Run: `.venv/Scripts/python -m pytest tests/test_runner.py -v`

- [ ] **Step 5: Implement** `app/runner.py`
```python
import asyncio
import codecs
import os
import random
import signal
import subprocess
import time
from dataclasses import dataclass

from app import checker, disk, forecast
from app.guard import Guard
from app.parser import (AlbumDelay, Finished, Line, LineSplitter, Progress, TrackDelay, TrackError,
                        TrackSkip, TrackStart, UrlStart, parse_line)
from app.settings import parse_range

DAY = 86400


@dataclass
class _Run:
    errors: int = 0
    finished: int | None = None
    busy: bool = False
    verdict: object = None
    paused_stop: bool = False
    tracks_done: int = 0
    last_lines: list = None


def _group_kwargs() -> dict:
    if os.name == "posix":
        return {"start_new_session": True}
    return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}


class Runner:
    def __init__(self, store, bus, cfg, sleep=asyncio.sleep, clock=time.time, rand=random.uniform):
        self.store, self.bus, self.cfg = store, bus, cfg
        self._sleep, self._clock, self._rand = sleep, clock, rand
        self.proc = None
        self.current_id = None
        self.live: dict = {}
        self.guard = Guard(3)
        self._lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._stopping = False
        self._last_finish = None
        self._pause_after_track = False
        self._cancel = False

    # ---- control -------------------------------------------------------
    def _state(self):
        self.bus.publish({"type": "state"})

    def wake(self):
        self._wake.set()

    def stop(self):
        self._stopping = True
        self._wake.set()

    async def pause(self):
        self.store.set_flag("paused", "1")
        if self.proc is not None:
            self._pause_after_track = True
        self._state()

    async def resume(self):
        self.store.set_flag("paused", None)
        self.store.clear_banner()
        self.guard.reset()
        self._pause_after_track = False
        self.wake()
        self._state()

    async def cancel(self, item_id: int):
        item = self.store.get_item(item_id)
        if not item:
            return
        if item_id == self.current_id and self.proc is not None:
            self._cancel = True
            self._signal(signal.SIGTERM)
        elif item["status"] in ("queued", "waiting"):
            self.store.update_item(item_id, status="cancelled", finished_at=self._clock())
        self._state()

    async def run_forever(self):
        while not self._stopping:
            if not await self.step():
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=2)
                except asyncio.TimeoutError:
                    pass

    # ---- scheduling ----------------------------------------------------
    def _halt(self, kind: str, reason: str):
        self.store.set_flag("paused", "1")
        self.store.set_banner(kind, reason)
        self._state()

    def _preflight(self, item):
        s = self.store.get_settings()
        now = self._clock()
        cap = s["max_tracks_per_24h"]
        if cap > 0 and self.store.count_tracks_since(now - DAY) >= cap:
            oldest = self.store.oldest_track_since(now - DAY)
            when = int(oldest + DAY) if oldest else 0
            return "cap_reached", f"{cap} tracks in 24 h; oldest counted track expires at epoch {when}"
        free = disk.free_bytes(self.cfg.disk_path)
        if free is not None:
            floor = s["low_disk_gb"] * 1_000_000_000
            if free < floor:
                return "disk_low", f"{free // 10**9} GB free, below {s['low_disk_gb']} GB"
            est = forecast.estimate_bytes(self.store, item)
            if free - est < floor:
                return "forecast", f"item needs about {est // 10**9} GB, {free // 10**9} GB free"
        return None

    async def step(self) -> bool:
        async with self._lock:
            banner = self.store.get_banner()
            s = self.store.get_settings()
            if (banner and banner["kind"] == "cap_reached" and s["auto_resume_after_cap"]
                    and self.store.count_tracks_since(self._clock() - DAY) < s["max_tracks_per_24h"]):
                await self.resume()
            if self.store.get_flag("paused") == "1":
                return False
            item = self.store.next_queued(self._clock())  # skips items still backing off (not_before)
            if not item:
                return False
            problem = self._preflight(item)
            if problem:
                self._halt(*problem)
                return False
            if not await self._album_delay(item):
                return False
            await self._run_item(item)
            return True

    async def _album_delay(self, item) -> bool:
        if self._last_finish is None:
            return True
        lo, hi = parse_range(self.store.get_settings()["album_delay"])
        remaining = self._rand(lo, hi) - (self._clock() - self._last_finish)
        if remaining <= 0:
            return True
        self.store.update_item(item["id"], status="waiting")
        self.current_id = item["id"]
        self.live = {"delay_kind": "album", "delay_until": self._clock() + remaining}
        self._state()
        try:
            while remaining > 0:
                cur = self.store.get_item(item["id"])
                if not cur or cur["status"] != "waiting":
                    return False  # cancelled or removed during the delay
                if self.store.get_flag("paused") == "1" or self._stopping:
                    return False
                nap = min(1.0, remaining)
                await self._sleep(nap)
                remaining -= nap
            return True
        finally:
            self.current_id, self.live = None, {}
            if self.store.get_item(item["id"])["status"] == "waiting":
                self.store.update_item(item["id"], status="queued")
            self._state()

    # ---- process -------------------------------------------------------
    def _signal(self, sig):
        p = self.proc
        if p is None or p.returncode is not None:
            return
        try:
            if os.name == "posix":
                os.killpg(p.pid, sig)
            elif sig == signal.SIGTERM:
                p.terminate()
            else:
                p.kill()
        except (ProcessLookupError, PermissionError):
            return
        asyncio.get_running_loop().create_task(self._kill_later(p))

    async def _kill_later(self, p):
        try:
            await asyncio.wait_for(p.wait(), 10)
        except asyncio.TimeoutError:
            try:
                if os.name == "posix":
                    os.killpg(p.pid, signal.SIGKILL)
                else:
                    p.kill()
            except (ProcessLookupError, PermissionError):
                pass

    async def _run_item(self, item):
        iid = item["id"]
        s = self.store.get_settings()
        env = os.environ.copy()
        env.update(GAMDL_TRACK_DELAY=s["track_delay"], GAMDL_ALBUM_DELAY=s["album_delay"],
                   GAMDL_STOREFRONT=s["storefront"])
        self.guard.threshold = s["error_threshold"]
        self.current_id, self.live = iid, {"track_pct": 0.0}
        self._pause_after_track = self._cancel = False
        self.store.clear_tracks(iid)
        self.store.update_item(iid, status="downloading", started_at=self._clock(), finished_at=None,
                               error_msg=None, errors=0, track_i=None, track_n=None, findings=None)
        self._state()
        argv = [*self.cfg.gamdl_cmd, *self.cfg.extra_args, item["url"]]
        try:
            self.proc = await asyncio.create_subprocess_exec(
                *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=env, **_group_kwargs())
        except OSError as e:
            self.store.update_item(iid, status="error", error_msg=f"cannot start gamdl-safe: {e}",
                                   finished_at=self._clock())
            self._halt("gamdl_missing", f"cannot start {self.cfg.gamdl_cmd[0]}")
            self.current_id, self.live = None, {}
            return
        run = _Run(last_lines=[])
        splitter, dec = LineSplitter(), codecs.getincrementaldecoder("utf-8")(errors="replace")
        while True:
            chunk = await self.proc.stdout.read(4096)
            if not chunk:
                break
            for line in splitter.feed(dec.decode(chunk)):
                self._on_line(iid, line, run)
        for line in splitter.flush():
            self._on_line(iid, line, run)
        rc = await self.proc.wait()
        self.proc = None
        await self._finish(item, run, rc)

    def _on_line(self, iid, raw, run):
        for ev in parse_line(raw):
            if isinstance(ev, (TrackStart, TrackSkip, TrackError, TrackDelay)):
                self.bus.publish({"type": "tracks", "item_id": iid})  # UI reloads the open track list
            v = self.guard.on_event(ev)
            if v and run.verdict is None:
                run.verdict = v
                self._signal(signal.SIGTERM)
            if isinstance(ev, UrlStart):
                self.store.update_item(iid, url_i=ev.n, url_n=ev.total)
            elif isinstance(ev, TrackStart):
                self.store.upsert_track(iid, ev.i, ev.title, "downloading")
                self.store.update_item(iid, track_i=ev.i, track_n=ev.total, current_title=ev.title, status="downloading")
                self.live = {"track_pct": 0.0, "speed": None}
                self._progress(iid)
            elif isinstance(ev, Progress):
                self.live.update(track_pct=ev.pct, speed=ev.speed, delay_kind=None, delay_until=None)
                self._progress(iid)
            elif isinstance(ev, TrackSkip):
                self.store.upsert_track(iid, ev.i, ev.title, "skipped", ev.reason)
            elif isinstance(ev, TrackError):
                run.errors += 1
                self.store.update_item(iid, errors=run.errors)
                cur = self.store.get_item(iid)
                if cur and cur["track_i"]:
                    self.store.upsert_track(iid, cur["track_i"], cur["current_title"], "error")
            elif isinstance(ev, TrackDelay):
                cur = self.store.get_item(iid)
                if cur and cur["track_i"]:
                    self.store.upsert_track(iid, cur["track_i"], cur["current_title"], "done")
                self.store.record_track(self._clock())
                run.tracks_done += 1
                self.live = {"track_pct": 100.0, "delay_kind": "track", "delay_until": self._clock() + ev.seconds}
                self.store.update_item(iid, status="waiting")
                self._progress(iid)
                if self._pause_after_track and run.verdict is None:
                    run.paused_stop = True
                    self._signal(signal.SIGTERM)
            elif isinstance(ev, AlbumDelay):
                self.live = {"delay_kind": "album", "delay_until": self._clock() + ev.seconds}
                self._progress(iid)
            elif isinstance(ev, Finished):
                run.finished = ev.errors
            elif isinstance(ev, Line):
                if "already running" in ev.text:
                    run.busy = True
                self.store.add_log(iid, ev.level, ev.text, self._clock())
                self.bus.publish({"type": "log", "item_id": iid, "level": ev.level, "text": ev.text, "ts": self._clock()})

    def _progress(self, iid):
        self.bus.publish({"type": "progress", "item_id": iid, "pct": self.live.get("track_pct"),
                          "speed": self.live.get("speed"), "delay_kind": self.live.get("delay_kind"),
                          "delay_until": self.live.get("delay_until")})

    def _fail(self, item, fields: dict, msg: str, errors=None):
        """Track errors that are not rate-limit/auth: retry the album after a backoff, then give up.
        gamdl skips files that already exist (overwrite=false), so a re-run only fetches what is missing."""
        s = self.store.get_settings()
        attempts = self.store.get_item(item["id"])["attempts"]
        if attempts < s["track_retries"]:
            lo, hi = parse_range(s["retry_backoff"])
            fields.update(status="queued", attempts=attempts + 1, not_before=self._clock() + self._rand(lo, hi),
                          error_msg=f"{msg}; retry {attempts + 1}/{s['track_retries']}")
        else:
            fields.update(status="error", error_msg=msg)
        if errors is not None:
            fields["errors"] = errors

    async def _finish(self, item, run, rc):
        iid, now = item["id"], self._clock()
        fields: dict = {"finished_at": now}
        if run.busy:
            fields.update(status="queued")
            self._halt("busy", "another gamdl-safe instance holds the lock")
        elif self._cancel:
            fields.update(status="cancelled")
        elif run.verdict:  # 429/403/auth always wins: never retried automatically
            fields.update(status="queued", error_msg=run.verdict.reason)
            self._halt(run.verdict.kind, run.verdict.reason)
        elif run.paused_stop:
            fields.update(status="queued")
        elif rc == 0 and run.finished is not None:
            errors = max(run.errors, run.finished)
            if errors == 0:
                fields.update(await asyncio.to_thread(self._describe_output, item))
                fields.update(status="done", errors=0, not_before=None)
                self.guard.reset()
            else:
                self._fail(item, fields, f"finished with {errors} error(s)", errors)
        else:
            self._fail(item, fields, f"gamdl-safe exited with code {rc} before finishing")
        self.store.update_item(iid, **fields)
        self.current_id, self.live = None, {}
        self._cancel = self._pause_after_track = False
        self._last_finish = now
        self._state()

    def _describe_output(self, item) -> dict:
        import json
        cur = self.store.get_item(item["id"])
        dirs = checker.find_output_dirs(self.cfg.staging_dir, cur["started_at"] or 0)
        if not dirs:
            return {}
        rules = checker.load_rules()
        findings: list = []
        codec, classification = "unknown", ""
        for d in dirs:  # runs in a worker thread (asyncio.to_thread): ffprobe is blocking
            for f in checker.check_album(d, cur["track_n"] or cur["expected_tracks"], rules):
                findings.append(f"{d.name}: {f}")
            codec, classification, codec_findings = checker.probe_album(d, rules)
            findings.extend(f"{d.name}: {f}" for f in codec_findings)
        path = str(dirs[0]) if len(dirs) == 1 else str(dirs[0].parent)
        return {"output_path": path, "size_bytes": checker.dir_size(dirs), "findings": json.dumps(findings),
                "codec": codec, "classification": classification}
```
Note for the implementer: `Runner.step` must remain the only entry point that starts processes; do not add any other `create_subprocess_exec` call.

- [ ] **Step 6: Run tests, fix until PASS**

Run: `.venv/Scripts/python -m pytest tests/test_runner.py tests/test_bus.py -v`
Expected: all PASS. Known timing risk: `test_rate_limit_auto_pauses_and_requeues` relies on the verdict having priority over exit code; it does by construction in `_finish`.

- [ ] **Step 7: Commit**
```bash
git add -A && git commit -m "feat: sequential runner with pause, guard, cap, disk gates and fake gamdl"
```

---

### Task 11: HTTP API and SSE

**Files:**
- Create: `app/api.py`, `app/__main__.py`, `tests/test_api.py`

**Interfaces:**
- Consumes: everything above.
- Produces `create_app(cfg: Config | None = None) -> FastAPI` and these routes (JSON):
  - `GET /api/state` -> `{now, items[], paused, banner, settings, disk:{free_bytes}, cookies, cap:{used,limit}, forecast_bytes, errors}`. Each item has `findings` decoded to a list and `live` for the running one.
  - `POST /api/parse` `{text}` -> `{results:[{raw, error, url, kind, id, storefront, duplicate}]}` (instant, no network).
  - `POST /api/preview` `{url}` -> `{preview, library, staging, duplicate, large}` where `library = {status, confidence, album, path, lossless, reasons, source ("metadata"|"catalog"), metadata_mtime, default_checked, needs_force}` and `status` is one of `in_library_lossless, in_library_lossy, similar, new, in_staging, unknown, unavailable`. `needs_force` is true for `in_library_lossless`, `similar`, `in_staging`; `default_checked` is its negation. If `metadata.csv` is unavailable the catalog name match is used and can only yield `similar` or `new`. The final status is remembered per normalized URL (`app.state.pcache`).
  - `POST /api/queue` `{items:[{url, title?, artist?, tracks?, force?:bool}]}` -> `{results:[{url, id, error}]}`. An item whose remembered preview status needs force is refused with a "use download anyway" error unless `force` is true; the accepted item stores `library_status`.
  - `POST /api/queue/{id}/{cancel|retry|retry_original|remove}` -> `{ok:true}`; `POST /api/queue/reorder` `{ids}`.
  - `POST /api/pause`, `POST /api/resume`, `GET|PUT /api/settings` (PUT returns 422 with `detail` on invalid values).
  - `GET /api/queue/{id}` -> item + `tracks`; `GET /api/history`; `GET /api/log?limit=`.
  - `GET /api/events` SSE (`data: {json}\n\n`, comment ping every 15 s).
  - Static files from `static/` at `/`.
- `python -m app` runs uvicorn with `cfg.host`/`cfg.port`.

- [ ] **Step 1: Write the failing tests** `tests/test_api.py`
```python
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
```

- [ ] **Step 2: Run, expect FAIL** (`ModuleNotFoundError: app.api`)

- [ ] **Step 3: Implement** `app/api.py`
```python
import asyncio
import json
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import disk, forecast
from app.bus import EventBus
from app.catalog import Catalog, staging_match
from app.config import Config, from_env
from app.cookies import cookie_status
from app.library import Library, LibraryMatch
from app.preview import PreviewService
from app.runner import Runner
from app.store import Store
from app.urls import UrlError, normalize, parse_many

STATIC = Path(__file__).resolve().parent.parent / "static"
PREVIEW_GETTER = None  # tests may replace this; None = real httpx getter
NEEDS_FORCE = {"in_library_lossless", "similar", "in_staging"}


class ParseIn(BaseModel):
    text: str


class PreviewIn(BaseModel):
    url: str


class QueueItemIn(BaseModel):
    url: str
    title: str | None = None
    artist: str | None = None
    tracks: int | None = None
    force: bool = False


class QueueIn(BaseModel):
    items: list[QueueItemIn]


class ReorderIn(BaseModel):
    ids: list[int]


def _public(item: dict, live=None) -> dict:
    out = dict(item)
    out["findings"] = json.loads(item["findings"]) if item.get("findings") else []
    if live is not None:
        out["live"] = live
    return out


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or from_env()
    store, bus = Store(cfg.db_path), EventBus()
    runner = Runner(store, bus, cfg)
    catalog = Catalog(cfg.catalog_path)
    library = Library(cfg.library_csv)

    @asynccontextmanager
    async def lifespan(app):
        if store.recover_after_crash():
            store.set_flag("paused", "1")
            store.set_banner("recovered", "restarted while a download was running; resume to continue")
        task = asyncio.create_task(runner.run_forever()) if cfg.autostart else None
        yield
        runner.stop()
        if task:
            task.cancel()

    app = FastAPI(lifespan=lifespan)

    def storefront() -> str:
        return store.get_settings()["storefront"]

    def snapshot() -> dict:
        s, now = store.get_settings(), time.time()
        items = [_public(i, dict(runner.live) if i["id"] == runner.current_id else None) for i in store.list_items()]
        return {
            "now": now, "items": items, "paused": store.get_flag("paused") == "1", "banner": store.get_banner(),
            "settings": s, "disk": {"free_bytes": disk.free_bytes(cfg.disk_path)},
            "cookies": cookie_status(cfg.cookies_path),
            "cap": {"used": store.count_tracks_since(now - 86400), "limit": s["max_tracks_per_24h"]},
            "forecast_bytes": forecast.queue_bytes(store), "errors": sum(i["errors"] for i in items),
        }

    def need(item_id: int) -> dict:
        item = store.get_item(item_id)
        if not item:
            raise HTTPException(404, "no such item")
        return item

    @app.get("/api/state")
    def state():
        return snapshot()

    @app.post("/api/parse")
    def parse(body: ParseIn):
        rows = []
        for r in parse_many(body.text, storefront()):
            p = r.parsed
            dup = bool(p and store.find_by_key(p.kind, p.id, p.track_id))
            rows.append({"raw": r.raw, "error": r.error, "url": p.normalized if p else None,
                         "kind": p.kind if p else None, "id": p.id if p else None,
                         "storefront": p.storefront if p else None, "duplicate": dup})
        return {"results": rows}

    @app.post("/api/preview")
    async def preview(body: PreviewIn):
        try:
            p = normalize(body.url, storefront())
        except UrlError as e:
            raise HTTPException(422, str(e))
        svc = app.state.previews
        pv = await svc.fetch(p)
        s = store.get_settings()
        lib = library.match(pv.remote(), s["library_exact"], s["library_similar"])
        source = "metadata"
        if lib.status == "unavailable" and pv.title:
            # metadata.csv unreadable: fall back to names from catalog.sqlite, which can never say "in library"
            cm = catalog.match(pv.artist, pv.title)
            source = "catalog"
            hit = cm.level in ("exact", "likely")
            lib = LibraryMatch("similar" if hit else "new", 0.0, "", cm.paths[0] if hit else "",
                               cm.lossless if hit else None, ["metadata.csv unavailable: catalog names only"])
        stg = staging_match(cfg.staging_dir, pv.artist, pv.title) if pv.title else None
        status = lib.status
        if status in ("new", "unknown", "unavailable") and stg and stg.level != "none":
            status = "in_staging"
        app.state.pcache[p.normalized] = status
        return {
            "preview": asdict(pv),
            "library": {**asdict(lib), "status": status, "source": source, "metadata_mtime": library.mtime(),
                        "default_checked": status not in NEEDS_FORCE, "needs_force": status in NEEDS_FORCE},
            "staging": asdict(stg) if stg else None,
            "duplicate": bool(store.find_by_key(p.kind, p.id, p.track_id)),
            "large": bool(pv.tracks and pv.tracks > s["preview_max_tracks"]),
        }

    app.state.previews = PreviewService(getter=None)
    app.state.pcache = {}

    @app.middleware("http")
    async def _late_getter(request: Request, call_next):
        # allows tests to swap PREVIEW_GETTER without rebuilding the app
        import app.api as me
        if me.PREVIEW_GETTER is not None:
            app.state.previews._get = me.PREVIEW_GETTER
        return await call_next(request)

    @app.post("/api/queue")
    def queue(body: QueueIn):
        results = []
        for it in body.items:
            try:
                p = normalize(it.url, storefront())
            except UrlError as e:
                results.append({"url": it.url, "id": None, "error": str(e)})
                continue
            if store.find_by_key(p.kind, p.id, p.track_id):
                results.append({"url": p.normalized, "id": None, "error": "already in queue or history"})
                continue
            status = app.state.pcache.get(p.normalized)
            if status in NEEDS_FORCE and not it.force:
                results.append({"url": p.normalized, "id": None,
                                "error": f"{status.replace('_', ' ')}: use download anyway to queue it"})
                continue
            new_id = store.add_item(p.normalized, p.original, p.kind, p.id, p.track_id, it.title, it.artist, it.tracks)
            if status:
                store.update_item(new_id, library_status=status)
            results.append({"url": p.normalized, "id": new_id, "error": None})
        runner.wake()
        bus.publish({"type": "state"})
        return {"results": results}

    @app.post("/api/queue/reorder")
    def reorder(body: ReorderIn):
        store.reorder(body.ids)
        bus.publish({"type": "state"})
        return {"ok": True}

    @app.get("/api/queue/{item_id}")
    def item_detail(item_id: int):
        item = _public(need(item_id))
        item["tracks"] = store.list_tracks(item_id)
        return item

    @app.post("/api/queue/{item_id}/{action}")
    async def item_action(item_id: int, action: str):
        item = need(item_id)
        running = item_id == runner.current_id
        if action == "cancel":
            await runner.cancel(item_id)
        elif action in ("retry", "retry_original"):
            if running:
                raise HTTPException(409, "item is running")
            fields = {"status": "queued", "error_msg": None, "errors": 0, "attempts": 0, "not_before": None}
            if action == "retry_original":
                fields["url"] = normalize(item["original_url"], None).normalized
            store.update_item(item_id, **fields)
            runner.wake()
        elif action == "remove":
            if running:
                raise HTTPException(409, "cancel the running item first")
            store.remove_item(item_id)
        else:
            raise HTTPException(404, "unknown action")
        bus.publish({"type": "state"})
        return {"ok": True}

    @app.post("/api/pause")
    async def pause():
        await runner.pause()
        return {"ok": True}

    @app.post("/api/resume")
    async def resume():
        await runner.resume()
        return {"ok": True}

    @app.get("/api/settings")
    def get_settings():
        return store.get_settings()

    @app.put("/api/settings")
    def put_settings(body: dict):
        try:
            out = store.put_settings(body)
        except ValueError as e:
            return JSONResponse({"detail": str(e)}, status_code=422)
        bus.publish({"type": "state"})
        return out

    @app.get("/api/history")
    def history():
        return [_public(i) for i in store.done_items()]

    @app.get("/api/log")
    def log(limit: int = 200):
        return store.tail_log(max(1, min(limit, 2000)))

    @app.get("/api/events")
    async def events():
        sub = bus.subscribe()

        async def gen():
            try:
                yield ": hello\n\n"
                while True:
                    try:
                        ev = await asyncio.wait_for(sub.get(), 15)
                        yield f"data: {json.dumps(ev)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": ping\n\n"
            finally:
                bus.unsubscribe(sub)

        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    STATIC.mkdir(exist_ok=True)
    app.mount("/", StaticFiles(directory=str(STATIC), html=True), name="static")
    return app
```

`app/__main__.py`:
```python
import uvicorn

from app.api import create_app
from app.config import from_env

cfg = from_env()
uvicorn.run(create_app(cfg), host=cfg.host, port=cfg.port, log_level="info")
```

Create a placeholder `static/index.html` containing `<!doctype html><title>gamdl</title><body>gamdl</body>` so `test_static_index_served` passes now; Task 12 replaces it.

- [ ] **Step 4: Run tests, fix until PASS; commit**
```bash
.venv/Scripts/python -m pytest -v
git add -A && git commit -m "feat: http api, sse events and app entrypoint"
```

---

### Task 12: Monochrome frontend

**Files:**
- Create/replace: `static/index.html`, `static/app.css`, `static/app.js`

**Interfaces:**
- Consumes: the Task 11 routes.
- Visual rules (from spec section 5 and Global Constraints) are binding: greys only, monospace, no gradients/shadows/emoji/icon circles, status by glyph + fill pattern, inverted blocks for error/paused/banners, 2 px focus ring, server text only via `textContent`, `aria-live=polite` on banner and status bar, `prefers-reduced-motion` respected, works at 375 px.

- [ ] **Step 1: Write** `static/index.html`
```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>gamdl queue</title>
<link rel="stylesheet" href="app.css">
</head>
<body>
<header id="bar" aria-live="polite"></header>
<div id="banner" role="status" aria-live="polite" hidden></div>
<main>
  <section id="add">
    <label for="urls">urls (one per line, any storefront; downloaded from /jp/)</label>
    <textarea id="urls" rows="3" spellcheck="false"></textarea>
    <div class="row"><button id="btn-preview">preview</button><span id="add-msg"></span></div>
    <table id="pv" hidden>
      <thead><tr><th></th><th>title</th><th>artist</th><th>tracks</th><th>library check</th><th></th></tr></thead>
      <tbody></tbody>
    </table>
    <div class="row" id="pv-actions" hidden><button id="btn-queue">queue selected</button></div>
  </section>
  <section id="queue">
    <div class="tablewrap">
    <table>
      <thead><tr><th><span class="sr">expand</span></th><th>#</th><th>status</th><th>album</th><th>track</th><th>album</th><th>track</th><th>speed</th><th></th></tr></thead>
      <tbody id="rows"></tbody>
    </table>
    </div>
    <p id="empty" hidden>queue is empty.</p>
  </section>
  <nav id="tabs" role="tablist">
    <button role="tab" data-tab="done" aria-selected="true">completed</button>
    <button role="tab" data-tab="settings">settings</button>
  </nav>
  <section id="panel"></section>
  <section id="logbox">
    <div class="row"><strong>log</strong>
      <select id="logfilter" aria-label="log filter"><option value="all">all</option><option value="warn">warn+</option><option value="error">error</option></select>
      <button id="logcopy">copy</button></div>
    <pre id="log" tabindex="0"></pre>
  </section>
</main>
<script src="app.js"></script>
</body>
</html>
```

- [ ] **Step 2: Write** `static/app.css`
```css
:root {
  --bg: #0b0b0b; --fg: #ededed; --dim: #8a8a8a; --line: #2e2e2e; --fill: #ededed; --inv-bg: #ededed; --inv-fg: #0b0b0b;
  font: 13px/1.45 ui-monospace, "Cascadia Mono", "SF Mono", Menlo, Consolas, monospace;
  font-variant-numeric: tabular-nums;
}
@media (prefers-color-scheme: light) {
  :root { --bg: #f4f4f4; --fg: #111; --dim: #666; --line: #cfcfcf; --fill: #111; --inv-bg: #111; --inv-fg: #f4f4f4; }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg); }
header#bar { display: flex; flex-wrap: wrap; gap: 4px 16px; align-items: center; padding: 8px 16px; border-bottom: 1px solid var(--line); font-size: 15px; }
header#bar .grow { flex: 1; }
#banner { padding: 8px 16px; background: var(--inv-bg); color: var(--inv-fg); }
main { padding: 0 16px 32px; }
section { margin-top: 16px; }
label, .dim { color: var(--dim); }
textarea, select, button, input { font: inherit; color: var(--fg); background: var(--bg); border: 1px solid var(--line); border-radius: 0; padding: 4px 8px; }
textarea { width: 100%; }
button { cursor: pointer; }
button:hover { border-color: var(--fg); }
:focus-visible { outline: 2px solid var(--fg); outline-offset: 2px; }
.row { display: flex; gap: 8px; align-items: center; margin-top: 8px; flex-wrap: wrap; }
.tablewrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; }
th, td { text-align: left; padding: 4px 8px; border-bottom: 1px solid var(--line); vertical-align: middle; white-space: nowrap; }
th { color: var(--dim); font-weight: normal; }
td.name { white-space: normal; min-width: 200px; }
tr.sel { background: color-mix(in srgb, var(--fg) 8%, transparent); }
.bar { width: 120px; height: 10px; border: 1px solid var(--fg); }
.bar > i { display: block; height: 100%; width: 0; background: var(--fill); }
.bar.wait > i { background: repeating-linear-gradient(-45deg, var(--fill) 0 2px, transparent 2px 5px); }
.st-downloading { font-weight: bold; }
.st-queued { color: var(--dim); }
.st-done { color: var(--dim); }
.st-error, .st-cancelled { background: var(--inv-bg); color: var(--inv-fg); padding: 0 4px; }
.icon { padding: 0 6px; }
#tabs { margin-top: 24px; display: flex; gap: 0; border-bottom: 1px solid var(--line); }
#tabs button { border-bottom: none; }
#tabs button[aria-selected="true"] { background: var(--inv-bg); color: var(--inv-fg); }
pre#log { height: 220px; overflow: auto; margin: 8px 0 0; padding: 8px; border: 1px solid var(--line); white-space: pre-wrap; word-break: break-word; }
pre#log .lvl-WARNING, pre#log .lvl-ERROR, pre#log .lvl-CRITICAL { font-weight: bold; }
ul.plain { margin: 4px 0; padding-left: 16px; }
.flag { background: var(--inv-bg); color: var(--inv-fg); padding: 0 4px; display: inline-block; }
.detail > td { padding: 0 0 8px 24px; border-bottom: 1px solid var(--line); }
table.tracks th, table.tracks td { padding: 2px 8px; }
table.tracks { width: auto; min-width: 60%; }
button[aria-expanded] { min-width: 24px; }
.sr { position: absolute; left: -9999px; }
.done-item { padding: 8px 0; border-bottom: 1px solid var(--line); }
.field { display: grid; grid-template-columns: 220px 1fr; gap: 8px; margin: 6px 0; align-items: center; }
@media (max-width: 640px) { .field { grid-template-columns: 1fr; } #queue th:nth-child(5), #queue td:nth-child(5) { display: none; } }
@media (prefers-reduced-motion: reduce) { * { transition: none !important; animation: none !important; } }
```

- [ ] **Step 3: Write** `static/app.js`
```js
"use strict";
const $ = (s, r = document) => r.querySelector(s);
function h(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) e.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat()) e.append(c instanceof Node ? c : String(c ?? ""));
  return e;
}
async function api(path, opt = {}) {
  const r = await fetch("/api/" + path, { headers: { "Content-Type": "application/json" }, ...opt, body: opt.body === undefined ? undefined : JSON.stringify(opt.body) });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || r.statusText);
  return data;
}
const fmtBytes = n => n == null ? "?" : n >= 1e9 ? (n / 1e9).toFixed(1) + " GB" : n >= 1e6 ? (n / 1e6).toFixed(1) + " MB" : Math.round(n / 1e3) + " KB";
const STATUS = { downloading: "▶ downloading", waiting: "◷ waiting", queued: "○ queued", done: "✓ done", error: "✕ error", cancelled: "– cancelled" };
const LIB = {
  in_library_lossless: "in library (lossless)", in_library_lossy: "in library (lossy)", similar: "similar (needs confirmation)",
  in_staging: "in staging", new: "new", unknown: "library data unavailable", unavailable: "library data unavailable",
};

let S = null, offset = 0, tab = "done", logFilter = "all", preview = [];
const expanded = new Set();   // item ids whose track list is open; everything starts collapsed
const trackCache = {};        // item id -> track rows

// single-track downloads (song urls, ?i= links, one-track albums) never get an expand toggle
const isAlbum = it => !(it.kind === "song" || it.track_id || it.track_n === 1 || it.expected_tracks === 1);

function bar(pct, wait) {
  const i = h("i"); i.style.width = Math.max(0, Math.min(100, pct || 0)) + "%";
  return h("div", { class: "bar" + (wait ? " wait" : ""), role: "progressbar", "aria-valuenow": Math.round(pct || 0) }, i);
}
function albumPct(it) {
  if (it.status === "done") return 100;
  if (!it.track_n) return 0;
  const done = it.status === "waiting" ? it.track_i : (it.track_i || 1) - 1;
  const cur = it.status === "downloading" ? (it.live?.track_pct || 0) / 100 : 0;
  return ((done + cur) / it.track_n) * 100;
}
function nowSrv() { return Date.now() / 1000 + offset; }
const countdown = (label, until) => `${label} ${Math.max(0, Math.round(until - nowSrv()))} s`;

// ---- expandable album detail -------------------------------------------
async function loadTracks(id) {
  try { trackCache[id] = (await api("queue/" + id)).tracks; } catch { trackCache[id] = []; }
  renderQueue();
  if (tab === "done") renderPanel();
}
function toggle(id) {
  if (expanded.has(id)) expanded.delete(id);
  else { expanded.add(id); loadTracks(id); }
  renderQueue();
  if (tab === "done") renderPanel();
}
function toggleBtn(it) {
  if (!isAlbum(it)) return h("span", {});
  const open = expanded.has(it.id);
  return h("button", { class: "icon", "aria-expanded": String(open), "aria-controls": "det-" + it.id,
    "aria-label": (open ? "collapse" : "expand") + " tracks", onclick: e => { e.stopPropagation(); toggle(it.id); } }, open ? "−" : "+");
}
function trackTable(id) {
  const rows = trackCache[id];
  if (!rows) return h("p", { class: "dim" }, "loading...");
  if (!rows.length) return h("p", { class: "dim" }, "no tracks yet.");
  const cls = s => "st-" + (["error", "done", "downloading"].includes(s) ? s : "queued");
  return h("table", { class: "tracks" },
    h("thead", {}, h("tr", {}, ["#", "status", "title", "note"].map(x => h("th", {}, x)))),
    h("tbody", {}, rows.map(t => h("tr", {}, h("td", {}, t.idx), h("td", { class: cls(t.status) }, t.status),
      h("td", { class: "name" }, t.title), h("td", { class: "dim" }, t.reason || "")))));
}

// ---- header, banner, queue --------------------------------------------
function renderBar() {
  const el = $("#bar"); el.replaceChildren();
  const run = S.items.find(i => i.status === "downloading" || i.status === "waiting");
  const state = S.paused ? "PAUSED" : run ? "RUNNING" : "IDLE";
  el.append(h("strong", {}, "gamdl"), h("span", {}, state),
    h("button", { onclick: () => api(S.paused ? "resume" : "pause", { method: "POST" }) }, S.paused ? "resume" : "pause"),
    h("span", { class: "grow" }),
    h("span", {}, `disk ${fmtBytes(S.disk.free_bytes)} free`),
    h("span", {}, `queue ~${fmtBytes(S.forecast_bytes)}`),
    h("span", {}, S.cap.limit ? `today ${S.cap.used}/${S.cap.limit}` : `today ${S.cap.used}`),
    h("span", {}, S.cookies.exists ? `cookies ${S.cookies.expiry_days == null ? "no expiry" : S.cookies.expiry_days < 0 ? "expired" : "expire " + S.cookies.expiry_days + " d"}` : "cookies missing"),
    h("span", {}, `errors ${S.errors}`));
}
function renderBanner() {
  const b = $("#banner"), msgs = [];
  if (S.banner) msgs.push(`${S.banner.kind}: ${S.banner.reason}. Fix the cause, then press resume.`);
  if (S.cookies.expired) msgs.push("cookies expired: re-export cookies.txt.");
  else if (S.cookies.exists && S.cookies.expiry_days != null && S.cookies.expiry_days <= 7) msgs.push(`cookies expire in ${S.cookies.expiry_days} d: re-export soon.`);
  b.hidden = msgs.length === 0; b.textContent = msgs.join(" ");
}
function move(it, dir) {
  const ids = S.items.map(i => i.id), k = ids.indexOf(it.id), j = k + dir;
  if (j < 0 || j >= ids.length) return;
  [ids[k], ids[j]] = [ids[j], ids[k]];
  api("queue/reorder", { method: "POST", body: { ids } });
}
const act = (it, what) => e => { e.stopPropagation(); api(`queue/${it.id}/${what}`, { method: "POST" }); };
function renderQueue() {
  const body = $("#rows"); body.replaceChildren();
  $("#empty").hidden = S.items.length > 0;
  S.items.forEach((it, n) => {
    const live = it.live || {};
    // countdown cells carry data-until/data-label so the 1 s tick only edits text (rows are not rebuilt, focus is kept)
    let right = live.speed || "", cd = null;
    if (live.delay_until) cd = { until: live.delay_until, label: `${live.delay_kind} delay` };
    else if (it.status === "waiting") right = "waiting";
    else if (it.status === "queued" && it.attempts && it.not_before) cd = { until: it.not_before, label: `retry ${it.attempts}/${S.settings.track_retries} in` };
    if (cd) right = countdown(cd.label, cd.until);
    const btn = (label, what, title) => h("button", { class: "icon", title, onclick: act(it, what) }, label);
    const actions = h("td", {},
      it.status === "queued" ? [h("button", { class: "icon", title: "move up", onclick: e => { e.stopPropagation(); move(it, -1); } }, "↑"),
                                h("button", { class: "icon", title: "move down", onclick: e => { e.stopPropagation(); move(it, 1); } }, "↓")] : "",
      ["queued", "downloading", "waiting"].includes(it.status) ? btn("cancel", "cancel") : "",
      ["error", "cancelled"].includes(it.status) ? [btn("retry", "retry"), btn("retry orig", "retry_original", "retry with the original storefront")] : "",
      !["downloading", "waiting"].includes(it.status) ? btn("remove", "remove") : "");
    const tr = h("tr", { tabindex: 0,
      onkeydown: e => { if (e.key === "[") move(it, -1); if (e.key === "]") move(it, 1); if (e.key === "Enter" && isAlbum(it) && e.target === tr) toggle(it.id); } },
      h("td", {}, toggleBtn(it)),
      h("td", {}, n + 1),
      h("td", { class: "st-" + it.status }, STATUS[it.status] || it.status),
      h("td", { class: "name" }, it.title ? `${it.title}${it.artist ? " / " + it.artist : ""}` : it.url,
        it.error_msg ? h("div", { class: "dim" }, it.error_msg) : ""),
      h("td", {}, it.track_n ? `${it.track_i || 0}/${it.track_n}` : "-"),
      h("td", {}, bar(albumPct(it), it.status === "waiting")),
      h("td", {}, bar(it.status === "done" ? 100 : live.track_pct, it.status === "waiting")),
      h("td", cd ? { "data-until": cd.until, "data-label": cd.label } : {}, right), actions);
    body.append(tr);
    if (isAlbum(it) && expanded.has(it.id)) {
      body.append(h("tr", { id: "det-" + it.id, class: "detail" }, h("td", {}), h("td", { colspan: 8 }, trackTable(it.id))));
    }
  });
}

// ---- completed / settings ---------------------------------------------
async function renderPanel() {
  const p = $("#panel"); p.replaceChildren();
  document.querySelectorAll("#tabs button").forEach(b => b.setAttribute("aria-selected", b.dataset.tab === tab));
  if (tab === "done") {
    const rows = await api("history");
    if (!rows.length) return p.append(h("p", { class: "dim" }, "nothing completed yet."));
    for (const r of rows) {
      const rel = (r.output_path || "").split(/[\\/]_gamdl-incoming[\\/]/)[1] || "";
      const smb = "\\\\192.168.18.225\\homelab\\hdd-backup\\music\\_gamdl-incoming\\" + rel.replaceAll("/", "\\");
      const open = expanded.has(r.id);
      p.append(h("div", { class: "done-item" },
        h("div", { class: "row" }, toggleBtn(r), h("strong", {}, r.title || r.url), h("span", { class: "dim" }, fmtBytes(r.size_bytes)),
          r.classification ? h("span", {}, `${r.codec}: ${r.classification}`) : ""),
        h("div", { class: "row" }, h("code", {}, smb), h("button", { onclick: () => navigator.clipboard?.writeText(smb) }, "copy path")),
        isAlbum(r) && open ? h("div", { id: "det-" + r.id, class: "detail" }, trackTable(r.id)) : "",
        r.findings.length ? h("ul", { class: "plain" }, r.findings.map(f => h("li", {}, f))) : h("div", { class: "dim" }, "no findings")));
    }
  } else {
    const s = await api("settings");
    const inputs = {};
    const fields = [["track_delay", "track delay s (min-max)"], ["album_delay", "album delay s (min-max)"], ["error_threshold", "pause after N 429/403"],
      ["low_disk_gb", "low disk GB"], ["max_tracks_per_24h", "max tracks / 24 h (0 = off)"], ["storefront", "storefront (empty = keep url's own)"],
      ["preview_max_tracks", "warn above N tracks"], ["track_retries", "album retries after a track error"], ["retry_backoff", "retry backoff s (min-max)"],
      ["library_exact", "library: in-library score"], ["library_similar", "library: similar score"]];
    for (const [k, label] of fields) { inputs[k] = h("input", { value: s[k], id: "f-" + k }); p.append(h("div", { class: "field" }, h("label", { for: "f-" + k }, label), inputs[k])); }
    const auto = h("input", { type: "checkbox", id: "f-auto", checked: s.auto_resume_after_cap });
    p.append(h("div", { class: "field" }, h("label", { for: "f-auto" }, "auto resume after cap"), auto));
    const msg = h("span", {});
    const text = ["track_delay", "album_delay", "storefront", "retry_backoff"];
    p.append(h("div", { class: "row" }, h("button", { onclick: async () => {
      const body = { auto_resume_after_cap: auto.checked };
      for (const [k] of fields) body[k] = text.includes(k) ? inputs[k].value : Number(inputs[k].value);
      try { await api("settings", { method: "PUT", body }); msg.textContent = "saved"; } catch (e) { msg.textContent = e.message; }
    } }, "save"), msg));
  }
}
function renderAll() { if (!S) return; renderBar(); renderBanner(); renderQueue(); renderPanel(); }

async function refresh() {
  S = await api("state"); offset = S.now - Date.now() / 1000; renderAll();
  for (const id of expanded) if (S.items.some(i => i.id === id)) loadTracks(id);
}

// ---- log and live events ----------------------------------------------
let logLines = [];
function addLog(l) { logLines.push(l); if (logLines.length > 500) logLines.shift(); drawLog(); }
function drawLog() {
  const rank = { DEBUG: 0, INFO: 0, WARNING: 1, ERROR: 2, CRITICAL: 2 }, min = { all: 0, warn: 1, error: 2 }[logFilter];
  const el = $("#log"), stick = el.scrollTop + el.clientHeight >= el.scrollHeight - 8;
  el.replaceChildren(...logLines.filter(l => (rank[l.level] ?? 0) >= min).map(l => h("div", { class: "lvl-" + l.level }, l.text)));
  if (stick) el.scrollTop = el.scrollHeight;
}
let pending = null, pendingTracks = new Set();
function scheduleRefresh() { if (!pending) pending = setTimeout(() => { pending = null; refresh(); }, 250); }
function scheduleTracks(id) {
  if (!expanded.has(id) || pendingTracks.has(id)) return;
  pendingTracks.add(id);
  setTimeout(() => { pendingTracks.delete(id); loadTracks(id); }, 250);
}
function connect() {
  const es = new EventSource("/api/events");
  es.onmessage = m => {
    const ev = JSON.parse(m.data);
    if (ev.type === "log") addLog(ev);
    else if (ev.type === "tracks") scheduleTracks(ev.item_id);
    else if (ev.type === "progress" && S) {
      const it = S.items.find(i => i.id === ev.item_id);
      if (it) { it.live = { track_pct: ev.pct, speed: ev.speed, delay_kind: ev.delay_kind, delay_until: ev.delay_until }; renderQueue(); }
    } else scheduleRefresh();
  };
  es.onerror = () => { es.close(); setTimeout(() => { refresh(); connect(); }, 2000); };
}

// ---- add flow: parse -> preview + library check -> queue ---------------
$("#btn-preview").onclick = async () => {
  const msg = $("#add-msg"), text = $("#urls").value;
  msg.textContent = "reading...";
  const { results } = await api("parse", { method: "POST", body: { text } });
  preview = results.map(r => ({ ...r, info: null, checked: false, force: false }));
  drawPreview(); msg.textContent = preview.length ? "" : "no urls found.";
  for (const row of preview) {
    if (!row.url) continue;
    try {
      row.info = await api("preview", { method: "POST", body: { url: row.url } });
      row.checked = row.info.library.default_checked && !row.duplicate;
    } catch (e) { row.info = { error: e.message }; row.checked = !row.duplicate; }
    drawPreview();
  }
};
function libCell(row) {
  const i = row.info;
  if (!row.url) return "";
  if (!i) return "checking...";
  if (i.error) return "preview failed: " + i.error;
  const l = i.library, parts = [];
  const head = h("div", { class: l.needs_force ? "flag" : "" },
    (LIB[l.status] || l.status) + (l.confidence ? ` (${Math.round(l.confidence * 100)}%)` : ""));
  if (l.album) parts.push(h("div", { class: "dim" }, `${l.album}${l.path ? " - " + l.path : ""}`));
  if (l.source === "catalog") parts.push(h("div", { class: "dim" }, "names only (metadata.csv unavailable)"));
  if (l.status === "in_library_lossy") parts.push(h("div", { class: "dim" }, "only a lossy copy exists; this download is AAC"));
  if (i.staging && i.staging.level !== "none") parts.push(h("div", { class: "dim" }, `staging: ${i.staging.paths[0]}`));
  if (i.large) parts.push(h("div", { class: "dim" }, "large: many tracks"));
  return [head, ...parts];
}
function drawPreview() {
  const t = $("#pv"), b = $("#pv tbody"); b.replaceChildren();
  t.hidden = $("#pv-actions").hidden = preview.length === 0;
  for (const r of preview) {
    const needs = !!r.info?.library?.needs_force;
    const cb = h("input", { type: "checkbox", checked: r.checked, disabled: !r.url || (needs && !r.force), "aria-label": "include",
      onchange: e => { r.checked = e.target.checked; } });
    const pv = r.info?.preview;
    const anyway = needs ? (r.force ? h("span", {}, "will download anyway")
      : h("button", { onclick: () => { r.force = true; r.checked = true; drawPreview(); } }, "download anyway")) : "";
    b.append(h("tr", {}, h("td", {}, cb),
      h("td", { class: "name" }, r.error ? `${r.raw}: ${r.error}` : pv?.title || r.url, r.duplicate ? h("div", { class: "dim" }, "already queued") : ""),
      h("td", {}, pv?.artist || ""), h("td", {}, pv?.tracks ?? ""), h("td", { class: "name" }, libCell(r)), h("td", {}, anyway)));
  }
}
$("#btn-queue").onclick = async () => {
  const items = preview.filter(r => r.checked && r.url).map(r => ({
    url: r.url, title: r.info?.preview?.title || null, artist: r.info?.preview?.artist || null,
    tracks: r.info?.preview?.tracks ?? null, force: !!r.force }));
  if (!items.length) return;
  const { results } = await api("queue", { method: "POST", body: { items } });
  const bad = results.filter(r => r.error);
  $("#add-msg").textContent = bad.length ? bad.map(r => r.error).join("; ") : `queued ${results.length}`;
  preview = []; drawPreview(); $("#urls").value = ""; refresh();
};
document.querySelectorAll("#tabs button").forEach(b => b.onclick = () => { tab = b.dataset.tab; renderPanel(); });
$("#logfilter").onchange = e => { logFilter = e.target.value; drawLog(); };
$("#logcopy").onclick = () => navigator.clipboard?.writeText(logLines.map(l => l.text).join("\n"));
setInterval(() => document.querySelectorAll("[data-until]").forEach(e => { e.textContent = countdown(e.dataset.label, Number(e.dataset.until)); }), 1000);

(async () => {
  logLines = (await api("log?limit=200").catch(() => [])).map(l => ({ level: l.level, text: l.text }));
  drawLog(); await refresh(); connect();
})();
```

- [ ] **Step 4: Run backend tests still pass**

Run: `.venv/Scripts/python -m pytest -v` -> PASS (the static test now sees "gamdl" in the title).

- [ ] **Step 5: Verify in a real browser with the fake wrapper**

Run the app in the background (forward slashes matter):
```bash
GAMDL_DASH_GAMDL_CMD="py -3 tools/fake_gamdl_safe.py" GAMDL_DASH_EXTRA_ARGS="" GAMDL_DASH_DB=data/dev.sqlite GAMDL_DASH_STAGING=data/staging GAMDL_DASH_DISK_PATH=. GAMDL_DASH_COOKIES=data/none.txt GAMDL_DASH_CATALOG=data/none.sqlite FAKE_OUT=data/staging FAKE_SLEEP=0.4 GAMDL_DASH_PORT=8110 .venv/Scripts/python -m app
```
Then with the built-in browser (`preview_start` with `url: http://127.0.0.1:8110`): paste the two example URLs, click preview, confirm both rows show the `/jp/` normalized URL and "checking..." then a result without crashing (preview will show `none` since the network fetch is allowed to fail), queue them, watch row 1 go ▶ downloading with a moving track bar, then album delay countdown before row 2, click pause, resume, cancel, and open both tabs (completed, settings). Then take screenshots at desktop width and at `resize_window` preset `mobile`, each with `colorScheme` light and dark. Check: greys only, no horizontal page scroll at 375 px, focus ring visible via Tab, banner shows after triggering the rate-limit scenario (restart with `FAKE_SCENARIO=rate_limit FAKE_TRACKS=6`).
Expandable detail (spec 8.10): album rows show a "+" button (collapsed by default, `aria-expanded="false"`); press it (mouse and Enter/Space) and the track list appears under the row and updates live while downloading, `aria-expanded="true"`; run once with `FAKE_TRACKS=1` and confirm the single-track item has NO toggle; finished albums show a toggle in the completed tab too. Preview: with the fake `FAKE_OUT` album already in `data/staging`, a matching URL shows "in staging" with a "download anyway" button and an unchecked box. Retry: with `FAKE_SCENARIO=fail` an item shows "retry 1/2 in N s" counting down.
Expected: no console errors (`read_console_messages onlyErrors`). Stop the server afterwards (`preview_stop`), delete `data/`.

- [ ] **Step 6: Commit**
```bash
git add -A && git commit -m "feat: monochrome queue dashboard frontend"
```

---

### Task 13: Deploy artifacts (proposal only), README, final verification

**Files:**
- Create: `deploy/gamdl-dashboard.service`, `deploy/README.md`, `README.md`

**Interfaces:** none. Nothing here is run against the server.

- [ ] **Step 1: Write** `deploy/gamdl-dashboard.service`
```ini
[Unit]
Description=gamdl dashboard
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/gamdl-dashboard
Environment=GAMDL_DASH_DB=/opt/gamdl-dashboard/data/dashboard.sqlite
Environment=GAMDL_DASH_PORT=8110
ExecStart=/opt/gamdl-dashboard/.venv/bin/python -m app
Restart=on-failure
KillMode=control-group
TimeoutStopSec=15

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Write** `README.md` (short) covering: what it is, `pip install -e ".[dev]"`, running tests, dev run with the fake wrapper (the command from Task 12), env vars table (from `app/config.py`), the safety rules (sequential, album delay owned by the dashboard, auto-pause, cookies never read), and a warning "bind is 0.0.0.0 by default; firewall so only LAN/Tailscale can reach 8110".

`deploy/README.md`: "PROPOSAL, not deployed. Deploy from the homelab-ops repo: git pull, lock in CURRENT_OPS.md, get user confirmation first." Steps to list (for the user, not executed): copy repo to `/opt/gamdl-dashboard`, create venv, install, install the unit, `systemctl enable --now`, then update `docs/services.md` and add the compose/systemd note in homelab-ops.

- [ ] **Step 3: Full verification**

Run: `.venv/Scripts/python -m pytest -v` -> all PASS. Then `git status` clean. Confirm with `grep`-style checks (use the Grep tool): no `innerHTML` in `static/app.js`; no `cookies_path` read anywhere except `app/cookies.py`; `create_subprocess_exec` appears only in `app/runner.py`.

- [ ] **Step 4: Open questions to raise with the user (do not act on them)**
  1. `gamdl-safe --help | grep no-exceptions` on the server: the default `GAMDL_DASH_EXTRA_ARGS=--no-exceptions` is unverified. Ask before running anything on the server.
  2. Preview parsing assumes schema.org JSON-LD on public album pages. With user approval, fetch one public page (`https://music.apple.com/jp/album/1791035368`) from this machine and compare against `parse_page`; adjust and add a fixture if it differs.
  3. Real 429/403 wording and exit codes: capture from the first supervised run and add a fixture to `tests/test_parser.py`.
  4. Align `app/checker_rules.json` with `docs/music-standards.md` (read-only from homelab-ops) once the user allows.

- [ ] **Step 5: Commit**
```bash
git add -A && git commit -m "docs: readme and deploy proposal"
```
