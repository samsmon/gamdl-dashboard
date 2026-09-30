# Deploy

`./deploy/install.sh` does everything below on a systemd Linux host (run `--help` for options):

1. creates `.venv` inside the checkout and `pip install -e .` (editable: the checkout must stay in place, `static/` is served from it; gamdl and the wrapper come with it);
2. writes `/etc/gamdl-dashboard.env` once (kept on re-runs unless `--force-env`);
3. renders `gamdl-dashboard.service` (no `PrivateTmp`: the wrapper lock lives in the shared tmp dir) and starts it.

Requirements: python >= 3.11 with venv support, `ffmpeg` (also provides `ffprobe`, used for codec detection).

Update: `git pull && .venv/bin/pip install -q -e . && sudo systemctl restart gamdl-dashboard`.

Config is via environment variables (see the table in the top-level README); edit `/etc/gamdl-dashboard.env` and restart.
`GAMDL_TRACK_DELAY`, `GAMDL_ALBUM_DELAY` and `GAMDL_STOREFRONT` are set by the dashboard for each wrapper process
from its settings. Do not set them in the env file.

Bind address defaults to `127.0.0.1`. The API has no authentication: if you bind to a LAN/VPN address, firewall it.
