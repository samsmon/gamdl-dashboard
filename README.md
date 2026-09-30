# gamdl dashboard

A web UI (FastAPI, SQLite, vanilla JS, no build step) that queues Apple Music
downloads and runs them one at a time through the `gamdl-safe` wrapper, with
live progress over Server-Sent Events.

## Install and test

```bash
pip install -e ".[dev]"
python -m pytest -v
```

## Dev run with the fake wrapper

```bash
GAMDL_DASH_GAMDL_CMD="py -3 tools/fake_gamdl_safe.py" GAMDL_DASH_EXTRA_ARGS="" GAMDL_DASH_DB=data/dev.sqlite GAMDL_DASH_STAGING=data/staging GAMDL_DASH_DISK_PATH=. GAMDL_DASH_COOKIES=data/none.txt GAMDL_DASH_CATALOG=data/none.sqlite FAKE_OUT=data/staging FAKE_SLEEP=0.4 GAMDL_DASH_PORT=8110 .venv/Scripts/python -m app
```

Then open http://localhost:8110. Delete `data/` afterwards.

## Environment variables

| Variable | Default |
|---|---|
| `GAMDL_DASH_DB` | `data/dashboard.sqlite` |
| `GAMDL_DASH_GAMDL_CMD` | `/usr/local/bin/gamdl-safe` |
| `GAMDL_DASH_EXTRA_ARGS` | `--no-exceptions` |
| `GAMDL_DASH_STAGING` | `/mnt/hdd-backup/music/_gamdl-incoming` |
| `GAMDL_DASH_DISK_PATH` | `/mnt/hdd-backup` |
| `GAMDL_DASH_COOKIES` | `/root/.gamdl/cookies.txt` |
| `GAMDL_DASH_CATALOG` | `/mnt/hdd-backup/music/catalog.sqlite` |
| `GAMDL_DASH_LIBRARY_CSV` | `/mnt/hdd-backup/music/metadata.csv` |
| `GAMDL_DASH_HOST` | `0.0.0.0` |
| `GAMDL_DASH_PORT` | `8110` |
| `GAMDL_DASH_AUTOSTART` | `1` (`0`, `false`, `no` disable) |

## Safety rules

- Strictly sequential: one `gamdl-safe` process at a time.
- The delay between albums is owned by the dashboard (settings `track_delay`, `album_delay`; floors 5 s and 30 s), passed to the wrapper through `GAMDL_TRACK_DELAY`, `GAMDL_ALBUM_DELAY`, `GAMDL_STOREFRONT`.
- The queue auto-pauses after N consecutive 429/403 responses (setting `error_threshold`, default 3). Resume is manual.
- Retries never happen after a 429/403 (or other guard verdict).
- The cookies file is never read by the dashboard; only its existence, mtime and age are reported.

## Library pre-check

Before queueing, each album is compared against the library CSV (`GAMDL_DASH_LIBRARY_CSV`) and
the staging catalog. Statuses: `in_library_lossless`, `in_library_lossy`, `similar`, `new`,
`unknown` (no album data from the Apple page), `unavailable` (CSV could not be read), and
`in_staging`. `in_library_lossless`, `similar` and `in_staging` require `force` to queue.
Score thresholds are the settings `library_exact` (0.85) and `library_similar` (0.55).

## Network warning

The bind is `0.0.0.0` by default. Firewall port 8110 so that only LAN/Tailscale can reach it.
The service has no authentication.

Deployment notes: see `deploy/README.md` (proposal only).
