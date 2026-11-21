#!/usr/bin/env bash
# Install/uninstall Devi Subhadra as a systemd *user* service (no root needed).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
UNIT_NAME="${UNIT_NAME:-devi-subhadra}"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT_FILE="$UNIT_DIR/$UNIT_NAME.service"
APP_HOST="${APP_HOST:-0.0.0.0}"
APP_PORT="${APP_PORT:-8000}"
UVICORN_BIN="$PROJECT_ROOT/.venv/bin/uvicorn"

usage() {
  cat <<EOF
Manage the Devi Subhadra systemd user service.

Usage:
  $0 install     Create/enable/start the unit and enable lingering
  $0 uninstall   Stop/disable the unit and delete the unit file
  $0 status      Show unit state, boot enablement, and lingering state
  $0 help        Show this help

Unit: $UNIT_FILE
Runs: $UVICORN_BIN main:create_production_app --factory --host $APP_HOST --port $APP_PORT
Cwd : $PROJECT_ROOT

Environment overrides:
  PROJECT_ROOT   Repo directory (default: parent of user_tools/)
  UNIT_NAME      systemd unit name, without .service (default: devi-subhadra)
  APP_HOST       Bind address (default: 0.0.0.0)
  APP_PORT       Bind port (default: 8000)
  XDG_CONFIG_HOME Used for the unit location (default: ~/.config)

Examples:
  ./user_tools/manage-agent-service.sh install
  APP_PORT=9000 ./user_tools/manage-agent-service.sh install
  ./user_tools/manage-agent-service.sh status
  ./user_tools/manage-agent-service.sh uninstall

Notes:
  * Run as your normal user, never with sudo/root: the service must read/write
    the repo's data/ directory with your ownership.
  * Requires a synced venv and the agent world:
      cd $PROJECT_ROOT && uv sync
      ./user_tools/create-agent-world-nolxc.sh
  * install runs "loginctl enable-linger \$USER" so the agent keeps running
    after logout. If polkit asks for a password and it fails, run manually:
      sudo loginctl enable-linger \$USER
  * Logs:   journalctl --user -u $UNIT_NAME -f
  * Reload: systemctl --user daemon-reload
EOF
}

require_user() {
  if [[ "$EUID" -eq 0 ]]; then
    echo "ERROR: run as your normal user (not sudo/root); this is a user unit." >&2
    exit 1
  fi
}

cmd_install() {
  require_user
  if [[ ! -x "$UVICORN_BIN" ]]; then
    echo "ERROR: $UVICORN_BIN not found. Run 'cd $PROJECT_ROOT && uv sync' first." >&2
    exit 1
  fi

  echo "[1/3] Writing $UNIT_FILE"
  install -d -m 0755 "$UNIT_DIR"
  cat >"$UNIT_FILE" <<EOF
[Unit]
Description=Devi Subhadra autonomous agent
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$PROJECT_ROOT
EnvironmentFile=-$PROJECT_ROOT/.env
Environment=TELEGRAM_POLLING=1
ExecStart=$UVICORN_BIN main:create_production_app --factory --host $APP_HOST --port $APP_PORT
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF

  echo "[2/3] Enabling and starting $UNIT_NAME.service"
  systemctl --user daemon-reload
  systemctl --user enable --now "$UNIT_NAME.service"

  echo "[3/3] Enabling linger for $USER"
  if ! loginctl enable-linger "$USER"; then
    echo "WARNING: could not enable linger; the service stops at logout." >&2
    echo "         Run: sudo loginctl enable-linger $USER" >&2
  fi

  systemctl --user --no-pager --lines=0 status "$UNIT_NAME.service" || true
}

cmd_uninstall() {
  require_user
  if [[ -f "$UNIT_FILE" ]]; then
    echo "[1/2] Stopping and disabling $UNIT_NAME.service"
    systemctl --user disable --now "$UNIT_NAME.service" || true
    rm -f "$UNIT_FILE"
    systemctl --user daemon-reload
  else
    echo "Unit $UNIT_FILE not found; nothing to disable."
  fi
  echo "[2/2] Removed. data/, database, and .env were left untouched."
  echo "  Linger stays enabled; disable with: sudo loginctl disable-linger $USER"
}

cmd_status() {
  require_user
  echo "Unit file : $UNIT_FILE $([[ -f "$UNIT_FILE" ]] && echo '(present)' || echo '(missing)')"
  echo "Enabled   : $(systemctl --user is-enabled "$UNIT_NAME.service" 2>/dev/null || true)"
  echo "Active    : $(systemctl --user is-active "$UNIT_NAME.service" 2>/dev/null || true)"
  echo "Linger    : $(loginctl show-user "$USER" -p Linger --value 2>/dev/null || echo unknown)"
  if [[ -f "$UNIT_FILE" ]]; then
    systemctl --user --no-pager --lines=10 status "$UNIT_NAME.service" || true
  fi
}

case "${1:-help}" in
  install) cmd_install ;;
  uninstall) cmd_uninstall ;;
  status) cmd_status ;;
  help|-h|--help) usage ;;
  *)
    echo "ERROR: unknown command: $1" >&2
    echo
    usage >&2
    exit 1
    ;;
esac
