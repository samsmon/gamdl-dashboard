# gamdl dashboard

A web UI (FastAPI, SQLite, vanilla JS, no build step) that queues Apple Music
downloads and runs them one at a time through the `gamdl-safe` wrapper, with
live progress over Server-Sent Events.

## Quick install (Linux, systemd)

```bash
git clone https://github.com/samsmon/gamdl-dashboard /opt/gamdl-dashboard
cd /opt/gamdl-dashboard
sudo apt install -y python3-venv ffmpeg          # once
sudo ./deploy/install.sh --host 0.0.0.0          # omit --host to listen on 127.0.0.1 only
```

The script creates the venv (gamdl and the `gamdl-safe` wrapper are part of this package, nothing else to
install), writes `/etc/gamdl-dashboard.env`, installs and starts the `gamdl-dashboard` service, and prints the URL.
Then export `cookies.txt` from `music.apple.com` (logged in, with a subscription) to `~/.gamdl/cookies.txt`
(or pass `--cookies FILE`); the dashboard warns until it holds a valid `media-user-token`. Options: `./deploy/install.sh --help`.
The API has no authentication: only bind beyond localhost on a trusted LAN/VPN.

## Dev install and test

```bash
pip install -e ".[dev]"
python -m pytest -v
```

## Dev run with the fake wrapper

```bash
GAMDL_DASH_GAMDL_CMD="py -3 tools/fake_gamdl_safe.py" GAMDL_DASH_EXTRA_ARGS="" GAMDL_DASH_DB=data/dev.sqlite GAMDL_DASH_STAGING=data/staging GAMDL_DASH_DISK_PATH=. GAMDL_DASH_COOKIES=data/none.txt GAMDL_DASH_CATALOG=data/none.sqlite FAKE_OUT=data/staging FAKE_SLEEP=0.4 GAMDL_DASH_PORT=8110 .venv/Scripts/python -m app
```

This command is for Windows only (`.venv/Scripts/python`, `py -3`); on Linux use `.venv/bin/python` and `python3`.

Then open http://localhost:8110. Delete `data/` afterwards.

## Environment variables

| Variable | Default |
|---|---|
| `GAMDL_DASH_DB` | `data/dashboard.sqlite` |
| `GAMDL_DASH_GAMDL_CMD` | `<python> -m app.gamdl_safe` (bundled wrapper; set to use an external `gamdl-safe`) |
| `GAMDL_DASH_EXTRA_ARGS` | `--no-exceptions` |
| `GAMDL_DASH_STAGING` | `/mnt/hdd-backup/music/_gamdl-incoming` |
| `GAMDL_DASH_DISK_PATH` | `/mnt/hdd-backup` |
| `GAMDL_DASH_COOKIES` | `/root/.gamdl/cookies.txt` |
| `GAMDL_DASH_CATALOG` | `/mnt/hdd-backup/music/catalog.sqlite` |
| `GAMDL_DASH_LIBRARY_CSV` | `/mnt/hdd-backup/music/metadata.csv` |
| `GAMDL_DASH_HOST` | `127.0.0.1` |
| `GAMDL_DASH_PORT` | `8110` |
| `GAMDL_DASH_AUTOSTART` | `1`; `0`, `false`, `no` start with the queue paused (the runner still runs, press resume) |

## Safety rules

- Strictly sequential: one `gamdl-safe` process at a time.
- The delay between albums is owned by the dashboard (settings `track_delay`, `album_delay`; floors 5 s and 30 s), passed to the wrapper through `GAMDL_TRACK_DELAY`, `GAMDL_ALBUM_DELAY`, `GAMDL_STOREFRONT`.
- The queue auto-pauses after N consecutive 429/403 responses or N consecutive track errors (setting `error_threshold`, default 3; the track-error count works even when gamdl prints no 429 text). Resume is manual.
- The daily track cap (`max_tracks_per_24h`) is checked before each item, so one large album can overshoot it. Low disk space and reaching the cap also pause the queue.
- The default `--no-exceptions` extra argument is accepted by gamdl 3.9.1 (verified 2026-09-30).
- The dashboard and any manual `gamdl-safe` run must share the host's `/tmp` for the wrapper lock; do not enable `PrivateTmp` in the service unit.
- Retries never happen after a 429/403 (or other guard verdict).
- The cookies file is never read by the dashboard; only its existence, mtime and age are reported.

## Library pre-check

Before queueing, each album is compared against the library CSV (`GAMDL_DASH_LIBRARY_CSV`); the
staging check runs only when that metadata status is `new`, `unknown` or `unavailable`. Statuses: `in_library_lossless`, `in_library_lossy`, `similar`, `new`,
`unknown` (no album data from the Apple page), `unavailable` (CSV could not be read), and
`in_staging`. `in_library_lossless`, `similar` and `in_staging` require `force` to queue.
Score thresholds are the settings `library_exact` (0.85) and `library_similar` (0.55).

## Network warning

The bind is `127.0.0.1` by default. If you bind to a LAN/Tailscale address, firewall port 8110 so that only LAN/Tailscale can reach it.
The API has no authentication. It refuses cross-origin state changes (Origin is checked on writes) but does not defend against DNS rebinding, so keep it firewalled to LAN/Tailscale.

Deployment notes: see `deploy/README.md` (proposal only).
