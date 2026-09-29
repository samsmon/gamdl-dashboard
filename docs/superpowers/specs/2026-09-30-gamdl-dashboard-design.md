# gamdl Dashboard: Design Spec

Date: 2026-09-30. Source requirements: `BRIEF.md`. Status: awaiting review.

## 1. Purpose
Queue and monitor Apple Music downloads through `gamdl-safe` from a qBittorrent/IDM-style web UI.
Runs on media-hosts (LXC 104), port 8110, LAN + Tailscale only.

**Hard constraints**
1. Strictly sequential: exactly one `gamdl-safe` process at any time (the wrapper flock is a second net, not the first).
2. Auto-pause after N consecutive 429/403 responses (default 3). Resume is manual only.
3. Cookie contents are never read into logs, DB, API responses or UI. Only existence, mtime and age.
4. No deployment from this repo. Deployment happens later from homelab-ops with user confirmation.

## 2. Decisions
- **Runtime:** systemd service on the host (chosen by default; user did not object). Calls `/usr/local/bin/gamdl-safe` directly and shares paths, config, cookies and flock. Docker artifacts are proposals only, not built in v1.
- **Stack:** Python 3.11+, FastAPI + uvicorn, SQLite (stdlib `sqlite3`), vanilla JS/HTML/CSS, no build step, no CDN (works offline on the LAN). Live updates by Server-Sent Events; commands by REST.
- **Bind:** `GAMDL_DASH_HOST` (default `0.0.0.0`), `GAMDL_DASH_PORT` (default `8110`). README warns to firewall.
- **One URL = one queue item.** Artist/playlist URLs are expanded by gamdl; the dashboard tracks `URL n/N` and `Track i/N` as sub-progress.

## 3. Architecture
```
app/
  parser.py   pure: text -> events (no I/O)
  guard.py    pure: events -> ok | pause(reason) | auth_alert
  store.py    SQLite: items, tracks, history, settings, log tail
  runner.py   single worker; the only module that spawns processes
  api.py      REST + SSE, static file serving
  disk.py     free space + low-space flag
  cookies.py  stat() only: exists, mtime, age in days
static/       index.html, app.css, app.js
tests/        parser, guard, store, runner (fake gamdl), api
tools/fake_gamdl_safe.py   replays fixture logs, for tests and local dev
```
Each unit is testable alone. `parser` and `guard` do no I/O; `runner` depends on them plus `store`.

### parser
Strips ANSI, splits on both `\r` and `\n`, emits typed events:
`url_start(n,N,url)`, `track_start(i,N,title)`, `track_skip(i,N,title,reason)`, `track_error(title)`,
`dl_progress(pct,size,speed,frag)`, `track_delay(s)`, `album_delay(s)`, `finished(errors)`, `line(level,text)`.
Classifies error text as `rate_limit` (429/403/"too many"), `auth` (cookie/login/subscription/401) or `other`.
Unrecognised lines pass through as `line`. Never raises on malformed input.

### guard
Counts consecutive `rate_limit` errors; any successful track resets the count. At the threshold: pause the queue and set banner `rate_limited`.
An `auth` error pauses and sets banner `cookies`. Both banners persist across restart until cleared by a manual resume.

### runner
One asyncio worker. Loop: if not paused and an item is queued, mark it `downloading` and spawn
`gamdl-safe --no-exceptions <url>` with env `GAMDL_TRACK_DELAY`/`GAMDL_ALBUM_DELAY` from settings, in its own process group.
It reads stdout+stderr, feeds the parser, writes the store and pushes SSE.
States: `queued, downloading, waiting, done, error, skipped, cancelled`. The queue has a global `paused` flag.
- Pause = finish the current track, then stop the process at the next `track_delay` line (SIGTERM to the group); the item returns to `queued` with progress kept.
- Cancel = SIGTERM to the group, SIGKILL after 10 s; item becomes `cancelled`.
- Startup: any item left `downloading`/`waiting` becomes `queued`, and the queue starts paused after an unclean shutdown.
- Exit code 0 with `finished(0)` = `done`; errors > 0 = `error` (retryable).
- Output path and size are computed after `done` from the album directory named in the log, using `os.scandir` totals only.
- If the flock is held by another process, report `busy` and do not spawn.

### API
`GET /api/state` snapshot; `GET /api/events` SSE; `POST /api/preview` and `POST /api/queue` (multiple urls, normalized to `/jp/` per 8.1, validated to `music.apple.com`, deduped, per-line results);
`POST /api/queue/{id}/{cancel|retry|remove}`; `POST /api/queue/reorder`; `POST /api/pause`, `/api/resume`;
`GET|PUT /api/settings`; `GET /api/history`. No endpoint ever returns cookie bytes. Log tail is capped at 2000 lines.

### Settings
Track delay range, album delay range, error threshold, low-disk threshold (GB). Validated: min <= max, sane bounds, and delays never below the wrapper defaults' lower bounds (5 s track, 30 s album) so the safety intent holds.

## 4. Interface
Single screen, dense, keyboard-friendly.

```
gamdl   RUNNING | Pause  Resume        disk 121 GB free   cookies 9 d   errors 0
-------------------------------------------------------------------------------
 #  Status       Album / Artist            Track   Album %        Track %  Speed
 1  DOWNLOADING  Whales Fall / Artist      6/17    ██████░░░░░░    62%     1.2 MB/s
 2  QUEUED       ...
-------------------------------------------------------------------------------
 tabs: Tracks | Completed | Settings        (detail pane for the selected row)
-------------------------------------------------------------------------------
 log (auto-scroll, filter: all/warn/error, copy)
```
- Banner row (full width, inverted) for `rate_limited`, `cookies`, `disk low`, `busy`. Text says what happened and the one action to take.
- The active row shows the delay countdown ("album delay 87 s") in place of speed while waiting.
- Row actions on hover/keyboard: cancel, retry, remove; reorder queued rows by drag or `[` / `]`.
- Completed tab: output path, size, and a copy button for the SMB path `\\192.168.18.225\homelab\hdd-backup\music\_gamdl-incoming\...`.
- Add box: textarea, paste many URLs, one per line.

## 5. Visual design: monochrome, no decoration
Principle: the interface is a tool, not a brand. Every pixel carries information.
- **Palette:** greys only, `#000` to `#fff`. Two themes via `prefers-color-scheme` (dark default), all from CSS custom properties. No accent color, no gradients, no shadows, no blur, no glow.
- **Status without color:** encoded by glyph, weight and fill pattern, never hue.
  `DOWNLOADING` solid fill bar, bold; `WAITING` hatched bar; `QUEUED` outline only; `DONE` dim text with a check glyph; `ERROR` and paused states an inverted block (light on dark or dark on light). Status word is always present as text for screen readers.
- **Type:** system monospace stack for data and headings alike (`ui-monospace, "Cascadia Mono", "SF Mono", Menlo, Consolas`), 13 px base, tabular numerals. One size step up for the header only. No webfonts.
- **Layout:** table on a strict 4 px grid, 1 px hairline borders, square corners (0-2 px). No cards-in-cards, no icon-in-circle, no emoji, no illustration, no hero, no mascots. Empty states are one plain sentence.
- **Motion:** none except progress width and a 1 s counter tick. Respects `prefers-reduced-motion`.
- **Copy:** literal and terse. No exclamation marks, no marketing language, no filler tooltips.
- **Accessibility:** text contrast >= 7:1 in both themes, visible 2 px focus ring, full keyboard operation, `aria-live=polite` on the status bar and banners.
- Verified by running the UI in a browser at desktop and 375 px widths in both themes before completion.

## 6. Failure handling
- gamdl binary missing or not executable: banner, queue paused, no crash.
- Process crash or nonzero exit without `finished`: item `error`, log retained.
- SSE drop: the client reconnects and refetches `/api/state`. The DB is the source of truth; SSE is an optimization only.
- Disk under threshold: banner, queue paused before starting the next item.

## 7. Testing
- pytest unit tests for parser (fixtures from the BRIEF log samples, including `\r` progress), guard, store and settings validation.
- Runner tests against `tools/fake_gamdl_safe.py`: success, skip, 429 x3 leading to auto-pause, auth failure, cancel mid-track, crash, restart recovery, and proof that two items never overlap.
- API tests via FastAPI `TestClient`, including "cookie content never appears in any response or log" using a canary cookie file.
- Manual browser pass for the UI. No real Apple or network calls in tests.

## 8. Added features (2026-09-30 review)

### 8.1 Multi-URL input and the /jp/ storefront
The add box accepts any number of URLs (newline, space or comma separated). Example input:
```
https://music.apple.com/jp/album/%E6%BA%9C%E6%81%AF/1791035368
https://music.apple.com/id/album/frozen-flower/1851922484
```
- **Normalization (`urls.py`, pure):** every URL is parsed and rewritten to the `jp` storefront: `https://music.apple.com/jp/<type>/<id>`. The slug is dropped because the numeric ID is what identifies the item; percent-encoding is decoded for display only. So the second example above is queued as `https://music.apple.com/jp/album/1851922484`. The original URL is kept on the item for display and debugging.
- Accepted types: `album`, `playlist`, `song`, `artist`, and `?i=<trackid>` song-in-album links. Anything not on `music.apple.com` is rejected per line with a reason; valid lines are still queued (partial success, with a per-line result list).
- Deduplicated by (type, id) against the queue and history.
- **Caveat:** an ID that exists in `id` may not exist in the `jp` storefront. If gamdl reports not-found/unavailable for a `/jp/` URL, the item becomes `error` with the reason "not available in jp storefront". It is never silently retried on another storefront, because the user asked for jp; the item offers a manual "retry as original storefront" action.
- The `/jp/` rule is a setting (`storefront`, default `jp`) so it lives in one place.

### 8.2 Preview before queueing
Pasted URLs are first resolved to a preview: title, artist, track count, release year. Nothing is queued until the user confirms the preview, and each row can be unchecked. Artist and playlist URLs show their expanded size, with a warning above a configurable track count (default 100). The metadata lookup goes through the same sequential runner lock and the same delay rules as downloads, so previewing counts against the request budget. If gamdl has no download-free metadata mode, the fallback is a direct Apple Music web page fetch of the public page title only, with no cookies, at most one request per URL with a random delay; if neither works, preview shows the normalized URL only and says so. This is verified in the first plan task, before anything depends on it.

### 8.3 Download cap
Setting `max_tracks_per_24h` (default 150, 0 = off). The store keeps a timestamped log of finished tracks; when the rolling 24 h count reaches the cap, the queue pauses with banner `cap_reached` and shows the time when the oldest counted track ages out. It resumes automatically only when the user has enabled `auto_resume_after_cap`; default is manual.

### 8.4 Library duplicate check (catalog.sqlite)
Source: `music/catalog.sqlite` (server path `/mnt/hdd-backup/music/catalog.sqlite`, SMB `\\192.168.18.225\homelab\hdd-backup\music\catalog.sqlite`). It is opened **read-only** (`mode=ro`, never written) and only when needed; the path is a setting. Observed schema: one table `tracks(id, relative_path UNIQUE, filename, category, format, size_bytes, is_lossless)`, ~25k rows, `relative_path` shaped `Lossless|Lossy/<Category>/<Artist ~>/<Album>/NN. Title.<ext>`. It holds no Apple IDs.
- Matching is therefore by names: normalize (NFKC, casefold, strip punctuation and the `~` suffix, keep both romaji and kanji parts) the preview's artist + album, then compare against the artist and album path segments; report `exact`, `likely` (fuzzy, with the matched path) or `none`. Also compare track count and whether the existing files are lossless.
- Shown in the preview as "already in library: Lossless/J-Pop/... (12 tracks, FLAC)". Warning only: the user decides. Because the dashboard downloads AAC, an existing lossless match is highlighted as "you already have a better copy".
- If the catalog is missing, stale or unreadable, the check shows "catalog unavailable" and never blocks queueing. The catalog is a file on a NAS that is rebuilt elsewhere, so it is treated as possibly out of date, and its mtime is shown.
- Staging folders (`_gamdl-incoming`) are also checked directly on disk by directory name, since they are not in the catalog.

### 8.5 Post-download checker
After an item reaches `done`, `checker.py` inspects the output folder (read-only) and lists problems against `docs/music-standards.md` conventions visible in the catalog: artist folder should follow `Romaji (Kanji) ~`, missing `Cover.jpg`, missing `.lrc` when lyrics are on, track file count differing from `Track n/N`, zero-byte files, disallowed characters. Findings appear in the Completed tab as a plain list per album. It never renames or moves anything. The rule set is a small data file in the repo so it can be changed without touching code; rules are limited to what the catalog and the brief demonstrate. If the real `music-standards.md` is provided, the rules are aligned to it in the plan.

### 8.6 Cookie expiry warning
`cookies.py` parses `cookies.txt` line by line and keeps only the **expiry timestamp** of each apple.com cookie: name and value are discarded immediately, never stored, logged or returned. The UI shows "earliest expiry in N days" and warns at 7 days or when expired. The parse is in memory and returns a single integer.

### 8.7 Disk forecast
Sum the expected size of queued items (preview track count x average bytes per track from completed history, with a fallback default of 9 MB/track for AAC) and compare to free space on `/mnt/hdd-backup`. The status bar shows "queue ~X GB, free Y GB"; a banner appears if the forecast exceeds free space minus the low-disk threshold, and the queue does not start an item that would not fit.

## 9. Updated interface additions
Add box becomes a two-step flow: paste -> preview table (title, artist, tracks, storefront-normalized URL, library match, checkbox) -> "Queue selected". Completed tab gains a per-album checker findings list. Status bar gains cap usage ("38/150 tracks today"), forecast and cookie expiry. New banners: `cap_reached`, `forecast`. All follow the monochrome rules in section 5.

## 10. Out of scope (v1)
Auth/login, multi-user, notifications, Docker image, moving or renaming files, editing gamdl `config.ini`, writing to `catalog.sqlite`, parallel downloads (never).

## 11. Open items
- Whether gamdl offers a download-free metadata mode (decides the preview mechanism, section 8.2); checked first in the plan.
- The real `docs/music-standards.md` is in homelab-ops and was not read; checker rules (8.5) are provisional until aligned to it.
- Exact wrapper exit codes and the precise 429/403 log wording are unverified. The parser is fixture-driven and will be tuned once a real error log is captured (user supplies one, or it is captured on the first supervised run).
