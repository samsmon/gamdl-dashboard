# Deploy (PROPOSAL, not deployed)

Nothing in this directory has been run on any server. Deployment happens later
from the separate homelab-ops repo: `git pull` there, take a lock in
`CURRENT_OPS.md`, and get explicit user confirmation before touching the host.

## Manual steps (for the user, not executed)

1. Copy this repo to `/opt/gamdl-dashboard` on media-hosts (LXC 104).
2. Create the venv: `python3 -m venv .venv`, then `.venv/bin/pip install -e .` (editable: the checkout must stay in place, because `static/` is served from the repo root).
3. Make sure `ffprobe` is on the server PATH (apt package `ffmpeg`). Codec detection needs it.
4. Copy `deploy/gamdl-dashboard.service` to `/etc/systemd/system/`.
5. `systemctl daemon-reload && systemctl enable --now gamdl-dashboard`.
6. Restrict port 8110 to LAN/Tailscale (firewall). The default bind is `0.0.0.0`.
7. In homelab-ops: update `docs/services.md` and add the compose/systemd note.

## Environment variables

Defaults come from `app/config.py`. The unit only sets `GAMDL_DASH_DB` and `GAMDL_DASH_PORT`.

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

`GAMDL_TRACK_DELAY`, `GAMDL_ALBUM_DELAY` and `GAMDL_STOREFRONT` are set by the
dashboard for each wrapper process from its settings. Do not set them in the unit.

The default `--no-exceptions` extra argument is unverified against the real wrapper.
