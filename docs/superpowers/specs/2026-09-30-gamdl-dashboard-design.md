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
`GET /api/state` snapshot; `GET /api/events` SSE; `POST /api/queue` (multi-line urls, validated to `music.apple.com`, deduped);
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

## 8. Out of scope (v1)
Auth/login, multi-user, notifications, Docker image, moving files into `Lossless/`/`Lossy/`, editing gamdl `config.ini`, parallel downloads (never).

## 9. Open items
- Exact wrapper exit codes and the precise 429/403 log wording are unverified. The parser is fixture-driven and will be tuned once a real error log is captured (user supplies one, or it is captured on the first supervised run).
