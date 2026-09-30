#!/usr/bin/env bash
# Install gamdl-dashboard as a systemd service. Safe to re-run (updates the venv and unit, keeps your env file).
#
#   git clone https://github.com/samsmon/gamdl-dashboard /opt/gamdl-dashboard
#   cd /opt/gamdl-dashboard && sudo ./deploy/install.sh
#
# Options:
#   --host ADDR       bind address (default 127.0.0.1; use 0.0.0.0 or a LAN IP to reach it from other machines.
#                     The API has NO authentication, so only do that on a trusted network)
#   --port N          default 8110
#   --staging DIR     where gamdl downloads to (default <repo>/data/downloads)
#   --cookies FILE    Netscape cookies.txt exported from music.apple.com (default ~/.gamdl/cookies.txt of the installing user)
#   --user NAME       run the service as this user instead of root
#   --library-csv F   optional: library metadata CSV for the "already in library?" check
#   --catalog F       optional: catalog.sqlite for the same check
#   --force-env       overwrite an existing /etc/gamdl-dashboard.env
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST=127.0.0.1 PORT=8110 STAGING="" COOKIES="" RUN_USER="" LIBCSV="" CATALOG="" FORCE_ENV=0
while [ $# -gt 0 ]; do
  case "$1" in
    --host) HOST=$2; shift 2;;
    --port) PORT=$2; shift 2;;
    --staging) STAGING=$2; shift 2;;
    --cookies) COOKIES=$2; shift 2;;
    --user) RUN_USER=$2; shift 2;;
    --library-csv) LIBCSV=$2; shift 2;;
    --catalog) CATALOG=$2; shift 2;;
    --force-env) FORCE_ENV=1; shift;;
    -h|--help) sed -n '2,19p' "$0"; exit 0;;
    *) echo "unknown option: $1" >&2; exit 2;;
  esac
done

[ "$(id -u)" -eq 0 ] || { echo "run as root (sudo ./deploy/install.sh)" >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' || { echo "python >= 3.11 is required" >&2; exit 1; }
python3 -c 'import ensurepip' 2>/dev/null || { echo "missing venv support: apt install python3-venv (or python3.X-venv)" >&2; exit 1; }
command -v ffmpeg >/dev/null || { echo "ffmpeg is required (gamdl remuxes with it): apt install ffmpeg" >&2; exit 1; }

RUN_HOME=$(getent passwd "${RUN_USER:-root}" | cut -d: -f6)
[ -n "$RUN_HOME" ] || { echo "no such user: $RUN_USER" >&2; exit 1; }
STAGING=${STAGING:-$REPO/data/downloads}
COOKIES=${COOKIES:-$RUN_HOME/.gamdl/cookies.txt}

echo "==> venv + dependencies (includes gamdl)"
python3 -m venv "$REPO/.venv"
"$REPO/.venv/bin/pip" install -q --upgrade pip
"$REPO/.venv/bin/pip" install -q -e "$REPO"
mkdir -p "$REPO/data" "$STAGING"
if [ -n "$RUN_USER" ]; then chown -R "$RUN_USER" "$REPO/data" "$STAGING"; fi

ENVF=/etc/gamdl-dashboard.env
if [ ! -f "$ENVF" ] || [ "$FORCE_ENV" = 1 ]; then
  echo "==> writing $ENVF"
  {
    echo "GAMDL_DASH_HOST=$HOST"
    echo "GAMDL_DASH_PORT=$PORT"
    echo "GAMDL_DASH_DB=$REPO/data/dashboard.sqlite"
    echo "GAMDL_DASH_STAGING=$STAGING"
    echo "GAMDL_DASH_DISK_PATH=$STAGING"
    echo "GAMDL_DASH_COOKIES=$COOKIES"
    echo "GAMDL_DASH_EXTRA_ARGS=--no-exceptions --output-path $STAGING --temp-path $STAGING/.tmp --cookies-path $COOKIES"
    [ -z "$LIBCSV" ] || echo "GAMDL_DASH_LIBRARY_CSV=$LIBCSV"
    [ -z "$CATALOG" ] || echo "GAMDL_DASH_CATALOG=$CATALOG"
    echo "# GAMDL_DASH_AUTOSTART=0   # start with the queue paused"
  } > "$ENVF"
  chmod 640 "$ENVF"
else
  echo "==> keeping existing $ENVF (use --force-env to overwrite)"
fi

echo "==> systemd unit"
USER_LINE=""; [ -z "$RUN_USER" ] || USER_LINE="User=$RUN_USER"$'\n'
sed -e "s|@REPO@|$REPO|g" -e "s|@USER_LINE@|$USER_LINE|" "$REPO/deploy/gamdl-dashboard.service" > /etc/systemd/system/gamdl-dashboard.service
systemctl daemon-reload
systemctl enable --now gamdl-dashboard
systemctl restart gamdl-dashboard
sleep 3
systemctl is-active gamdl-dashboard

envget() { grep -m1 "^$1=" "$ENVF" | cut -d= -f2-; }   # the file is not shell-safe (unquoted spaces), never source it
H=$(envget GAMDL_DASH_HOST); P=$(envget GAMDL_DASH_PORT); C=$(envget GAMDL_DASH_COOKIES)
echo
echo "Dashboard: http://${H/0.0.0.0/localhost}:${P}"
if [ ! -f "$C" ]; then
  echo "NEXT: export cookies.txt from music.apple.com (logged in, with a subscription) to $C"
  echo "      it needs a 'media-user-token' cookie; the dashboard shows a warning until it is valid."
fi
