#!/usr/bin/env bash
# Install or update the Job Alert Engine as a systemd service on Linux.
# Usage (from the project folder): sudo ./deploy/install.sh
set -euo pipefail

APP_DIR=/opt/job-alert-engine
SERVICE=job-notifier
APP_USER=jobnotifier
SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"

if [[ $EUID -ne 0 ]]; then
  echo "Run as root: sudo $0" >&2
  exit 1
fi

command -v python3 >/dev/null || { echo "python3 not found (sudo apt install -y python3 python3-venv rsync)"; exit 1; }
command -v rsync >/dev/null || { echo "rsync not found (sudo apt install -y rsync)"; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' \
  || { echo "Python 3.9+ required"; exit 1; }

id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"

mkdir -p "$APP_DIR/data"
rsync -a --delete \
  --exclude '.venv' --exclude 'data' --exclude '.env' --exclude 'config.json' \
  --exclude '__pycache__' --exclude '.git' \
  "$SRC_DIR/" "$APP_DIR/"

if [[ ! -f "$APP_DIR/.env" ]]; then
  if [[ -f "$SRC_DIR/.env" ]]; then
    cp "$SRC_DIR/.env" "$APP_DIR/.env"
    echo ">> Copied existing .env to $APP_DIR/.env"
  else
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    echo ">> Created $APP_DIR/.env from template (edit before starting)"
  fi
fi

if [[ ! -f "$APP_DIR/config.json" ]]; then
  if [[ -f "$SRC_DIR/config.json" ]]; then
    cp "$SRC_DIR/config.json" "$APP_DIR/config.json"
    echo ">> Copied existing config.json to $APP_DIR/config.json"
  else
    cp "$APP_DIR/config.example.json" "$APP_DIR/config.json"
    echo ">> Created $APP_DIR/config.json from template"
  fi
fi

[[ -d "$APP_DIR/.venv" ]] || python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

chown -R "$APP_USER:$APP_USER" "$APP_DIR"
chmod 600 "$APP_DIR/.env"

install -m 644 "$APP_DIR/deploy/$SERVICE.service" "/etc/systemd/system/$SERVICE.service"
systemctl daemon-reload
systemctl enable "$SERVICE"
systemctl restart "$SERVICE"

echo
echo "Installed and started $SERVICE. Useful commands:"
echo "  journalctl -u $SERVICE -f"
echo "  sudo -u $APP_USER $APP_DIR/.venv/bin/python $APP_DIR/manage.py status"
echo "  sudo -u $APP_USER $APP_DIR/.venv/bin/python $APP_DIR/manage.py list-users"
echo "  sudo -u $APP_USER $APP_DIR/.venv/bin/python $APP_DIR/manage.py add-user"
echo "  sudo -u $APP_USER $APP_DIR/.venv/bin/python $APP_DIR/main.py --test-fetch"
echo "  sudo -u $APP_USER $APP_DIR/.venv/bin/python $APP_DIR/main.py --test-email you@example.com"
echo "  sudo systemctl reload $SERVICE"
