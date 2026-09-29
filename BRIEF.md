# Handoff: gamdl Dashboard (build in a SEPARATE session/workspace)

> homelab-ops sessions must not scaffold new apps (see CLAUDE.md). This is the brief for a new project
> workspace. Deployment target/ops back in homelab-ops afterwards.

## Goal
Web dashboard to queue and monitor Apple Music downloads via `gamdl`, UI in the style of qBittorrent /
IDM: queue table, per-album + per-track progress bars, speed, status, pause/resume/cancel, log panel.

## Access & deployment
- Runs on `media-hosts` (LXC 104, `192.168.18.229`, Tailscale `100.113.250.97`). LAN + Tailscale only, never public.
- Port: **8110** (checked free on media-hosts and absent from `docs/services.md` as of 2026-09-29).
- Deploy as a Docker container (compose file -> `configs/docker-compose/gamdl-dashboard.yml` in homelab-ops)
  or a systemd service. Decide in the new project; the backend needs to run gamdl on the host or in a
  container that has the same paths/config (see below).
- Nginx Proxy Manager entry optional.

## Existing gamdl setup (already working, verified 2026-09-29)
- gamdl 3.9.1 via pipx: venv `/opt/pipx/venvs/gamdl`, bin `/usr/local/bin/gamdl`. ffmpeg via apt.
- Wrapper `/usr/local/bin/gamdl-safe` (repo copy: `scripts/gamdl-safe.py`): monkeypatches gamdl to add
  random delays, then runs the normal CLI. All gamdl args pass through.
  - `GAMDL_TRACK_DELAY` default `8-20` s after each downloaded track (2x on error).
  - `GAMDL_ALBUM_DELAY` default `60-180` s before each URL after the first.
  - flock `/tmp/gamdl-safe.lock`: only ONE instance at a time. The dashboard must serialize the queue
    (one URL at a time), never run parallel gamdl processes. Purpose: avoid Apple flagging/banning the account.
- Config: `/root/.gamdl/config.ini` (`[gamdl]`). Key values: `output_path=/mnt/hdd-backup/music/_gamdl-incoming`,
  `temp_path=.../_gamdl-incoming/.tmp`, `cookies_path=/root/.gamdl/cookies.txt`, `use_wrapper=false`,
  `overwrite=false`, `song_codec_piority=aac-web` (AAC, NOT lossless), `download_mode=ytdlp`, lrc lyrics on.
  NOTE: gamdl rewrites config.ini with defaults on run; check values after upgrades.
- Cookies: Netscape `cookies.txt`, apple.com only, mode 600. Expire eventually; the dashboard should surface
  auth failures and prompt for re-export. Never log/store cookie contents.
- Output layout: `<output>/<Artist>/<Album>/NN Title.m4a` + `.lrc` + `Cover.jpg`. Staging only; files are
  moved into `Lossless/`/`Lossy/` manually per `docs/music-standards.md` (artist `Romaji (Kanji) ~` etc).

## Log format to parse (from a real run, ANSI colors stripped)
```
[INFO     19:03:47] [Track   1/17 ] Downloading "The City Where Whales Fall"
[gamdl-safe] track delay 5s
[gamdl-safe] album delay 87s
[WARNING  ...] [Track   6/17 ] Skipping "End Roll": <reason>
[ERROR    ...] / Error downloading "<title>"
[INFO     19:08:12] Finished with 0 error(s)
[INFO     ...] URL   1/3  Processing "<url>"
```
yt-dlp emits `[download]  4.4% of ~ 22.57KiB at 8.49KiB/s ... (frag 0/23)` using `\r` (carriage returns), so
split on `\r` as well as `\n`. Run gamdl with `--log-file` and/or `--no-exceptions` for cleaner output.

## Functional requirements
1. Add URLs (album/playlist/artist/song), paste many at once; queue is persisted (survives restart).
2. Strictly sequential processing via `gamdl-safe`; show album delay countdown and track delay state.
3. Per-item view: album title/artist, track n/total, current track progress %, speed, status
   (queued/downloading/waiting/done/error/skipped).
4. Controls: pause after current track, resume, cancel item, retry failed, reorder, remove.
5. Live log panel, error count, disk free for `/mnt/hdd-backup` (was 94% full, ~121 GB free at setup: warn low).
6. Settings: delay ranges (write env for the wrapper), pause/stop-on-N-consecutive-errors (429/403 => auto-pause
   to protect the account), cookie status/age.
7. Completed list with output path and size; button to open the path hint (SMB
   `\\192.168.18.225\homelab\hdd-backup\music\_gamdl-incoming\...`).
8. No auth required beyond LAN/Tailscale, but bind to internal interfaces only.

## Known issues to design around
- `media-hosts` DNS: Tailscale MagicDNS was broken; fixed 2026-09-29 by `tailscale set --accept-dns=false` and
  `/etc/resolv.conf` -> `192.168.18.1`, `1.1.1.1`. If the dashboard runs in a container, ensure it resolves too.
- LXC 104 is unprivileged: `chown 100000:100000` fails from inside; use `chmod 777` on staging dirs.
- Rate-limit safety is a hard requirement, not optional.
