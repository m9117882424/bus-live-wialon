#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/bus-live-wialon"
REPO_URL="${1:-}"

if [ -z "$REPO_URL" ]; then
  echo "Usage: sudo ./deploy_server.sh https://github.com/<owner>/<repo>.git"
  exit 1
fi

sudo apt update
sudo apt install -y python3 python3-venv python3-pip git nginx

if [ ! -d "$APP_DIR/.git" ]; then
  sudo rm -rf "$APP_DIR"
  sudo git clone "$REPO_URL" "$APP_DIR"
else
  cd "$APP_DIR"
  sudo git pull
fi

cd "$APP_DIR"

sudo python3 -m venv .venv
sudo .venv/bin/pip install --upgrade pip
sudo .venv/bin/pip install -r requirements.txt

if [ ! -f "$APP_DIR/.env" ]; then
  sudo cp "$APP_DIR/.env.example" "$APP_DIR/.env"
  echo "Created $APP_DIR/.env. Edit WIALON_TOKEN before starting service:"
  echo "sudo nano $APP_DIR/.env"
fi

sudo cp "$APP_DIR/deploy/bus-live-wialon.service" /etc/systemd/system/bus-live-wialon.service
sudo systemctl daemon-reload
sudo systemctl enable bus-live-wialon
sudo systemctl restart bus-live-wialon

sudo systemctl status bus-live-wialon --no-pager
