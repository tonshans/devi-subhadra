#!/usr/bin/env bash
# Create the LXC "agent world": UID/GID mapping, bind mounts, Playwright setup, and Genesis DB seed.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
CONTAINER_NAME="${CONTAINER_NAME:-devi-subhadra}"
LXC_PATH="${LXC_PATH:-/var/lib/lxc}"
DISTRO="${DISTRO:-ubuntu}"
RELEASE="${RELEASE:-noble}"
ARCH="${ARCH:-amd64}"

WITH_ANTIGRAVITY="${WITH_ANTIGRAVITY:-0}"
AGY_STATE_DIR="${AGY_STATE_DIR:-$PROJECT_ROOT/mnt/agy_state}"
AGY_INSTALL_URL="${AGY_INSTALL_URL:-}"

ARG_COUNT=$#
while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-antigravity) WITH_ANTIGRAVITY=1 ;;
    --help|-h)
      echo "Usage: sudo $0 [--with-antigravity]"
      exit 0
      ;;
    *)
      echo "ERROR: unknown argument: $1" >&2
      exit 1
      ;;
  esac
  shift
done
if [[ "$ARG_COUNT" -eq 0 && "$WITH_ANTIGRAVITY" != "1" && -t 0 ]]; then
  read -r -p "Install Antigravity CLI (agy) with persistent auth? [y/N] " reply || true
  [[ "$reply" =~ ^[Yy]$ ]] && WITH_ANTIGRAVITY=1
fi

WORKSPACE_DIR="${WORKSPACE_DIR:-$PROJECT_ROOT/mnt/workspace}"
ATELIER_DIR="${ATELIER_DIR:-$PROJECT_ROOT/mnt/agent_atelier}"
DATA_DIR="${DATA_DIR:-$PROJECT_ROOT/data}"
DB_PATH="${DB_PATH:-$DATA_DIR/devi_subhadra.db}"

# Map the invoking host user to container uid/gid 1000 so bind mounts stay writable.
HOST_UID="${HOST_UID:-${SUDO_UID:-$(id -u)}}"
HOST_GID="${HOST_GID:-${SUDO_GID:-$(id -g)}}"
if [[ "$HOST_UID" == "0" ]]; then HOST_UID=1000; fi
if [[ "$HOST_GID" == "0" ]]; then HOST_GID=1000; fi
CONTAINER_UID="${CONTAINER_UID:-1000}"
CONTAINER_GID="${CONTAINER_GID:-1000}"

PYTHON_BIN="${PYTHON_BIN:-$PROJECT_ROOT/.venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then PYTHON_BIN="$(command -v python3 || true)"; fi
if [[ -z "$PYTHON_BIN" ]]; then
  echo "ERROR: python3 not found. Install Python 3.11+ first." >&2
  exit 1
fi

if [[ "$EUID" -ne 0 ]]; then
  echo "ERROR: run as root (LXC config and UID mapping need it), e.g. sudo $0" >&2
  exit 1
fi
for command in lxc-create lxc-start lxc-info; do
  command -v "$command" >/dev/null || {
    echo "ERROR: missing '$command'. Install LXC first." >&2
    exit 1
  }
done

echo "[1/7] Preparing host directories"
install -d -o "$HOST_UID" -g "$HOST_GID" -m 0755 \
  "$WORKSPACE_DIR" "$ATELIER_DIR" "$DATA_DIR"
if [[ "$WITH_ANTIGRAVITY" == "1" ]]; then
  # 700 + host ownership: container uid 1000 is mapped to HOST_UID on the host.
  install -d -o "$HOST_UID" -g "$HOST_GID" -m 0700 "$AGY_STATE_DIR"
fi

echo "[2/7] Creating container '$CONTAINER_NAME' (if missing)"
if [[ ! -d "$LXC_PATH/$CONTAINER_NAME" ]]; then
  lxc-create -n "$CONTAINER_NAME" -t download -- \
    -d "$DISTRO" -r "$RELEASE" -a "$ARCH"
fi

echo "[3/7] Applying UID/GID mapping and bind mounts"
CONFIG_FILE="$LXC_PATH/$CONTAINER_NAME/config"
AGY_MOUNT=""
if [[ "$WITH_ANTIGRAVITY" == "1" ]]; then
  AGY_MOUNT="lxc.mount.entry = $AGY_STATE_DIR home/agent/.config/agy none bind,create=dir 0 0"
fi
sed -i '/# BEGIN devi-subhadra/,/# END devi-subhadra/d' "$CONFIG_FILE"
cat >> "$CONFIG_FILE" <<EOF
# BEGIN devi-subhadra
lxc.idmap = u 0 100000 $CONTAINER_UID
lxc.idmap = u $CONTAINER_UID $HOST_UID 1
lxc.idmap = u $((CONTAINER_UID + 1)) 101000 $((65536 - CONTAINER_UID - 1))
lxc.idmap = g 0 100000 $CONTAINER_GID
lxc.idmap = g $CONTAINER_GID $HOST_GID 1
lxc.idmap = g $((CONTAINER_GID + 1)) 101000 $((65536 - CONTAINER_GID - 1))
lxc.mount.entry = $WORKSPACE_DIR mnt/workspace none bind,create=dir,optional 0 0
lxc.mount.entry = $ATELIER_DIR mnt/agent_atelier none bind,create=dir,optional 0 0
$AGY_MOUNT
lxc.apparmor.profile = generated
# END devi-subhadra
EOF

echo "[4/7] Starting container"
if ! lxc-info -n "$CONTAINER_NAME" -s 2>/dev/null | grep -q RUNNING; then
  lxc-start -n "$CONTAINER_NAME"
fi
for _ in $(seq 1 30); do
  if lxc-info -n "$CONTAINER_NAME" -s 2>/dev/null | grep -q RUNNING; then break; fi
  sleep 1
done
lxc-info -n "$CONTAINER_NAME" -s | grep -q RUNNING || {
  echo "ERROR: container failed to start. Check: lxc-start -n $CONTAINER_NAME -F" >&2
  exit 1
}

echo "[5/7] Configuring Antigravity CLI (agy)"
if [[ "$WITH_ANTIGRAVITY" == "1" ]]; then
  lxc-attach -n "$CONTAINER_NAME" -- bash -c '
set -e
# uid 1000 is the only container uid mapped to the host user; keep agent there.
if ! id -u agent >/dev/null 2>&1; then
  existing="$(getent passwd 1000 | cut -d: -f1 || true)"
  if [[ -n "$existing" ]]; then
    old_group="$(id -gn "$existing")"
    usermod -l agent -d /home/agent -m "$existing"
    groupmod -n agent "$old_group" || true
  else
    useradd -m -u 1000 -s /bin/bash agent
  fi
fi
if ! command -v sudo >/dev/null 2>&1; then
  apt-get update && apt-get install -y sudo
fi
if ! command -v agy >/dev/null 2>&1; then
  if [[ -z "$1" ]]; then
    echo "WARNING: AGY_INSTALL_URL is not set; agy was not installed." >&2
  else
    curl -fsSL "$1" | bash
  fi
fi
install -d -o agent -g agent -m 0700 /home/agent/.config/agy
chown -R agent:agent /home/agent/.config/agy
' _ "$AGY_INSTALL_URL"
else
  echo "  skipped (pass --with-antigravity to enable)"
fi

echo "[6/7] Installing Playwright Chromium browser"
# Memastikan library browser untuk HeadlessBrowserPlugin tersedia
if ! "$PYTHON_BIN" -m playwright install chromium; then
    echo "WARNING: Failed to install Playwright browser. Did you run 'uv sync' first?" >&2
fi

echo "[7/7] Seeding Genesis DB at $DB_PATH"
"$PYTHON_BIN" - "$PROJECT_ROOT" "$DB_PATH" "${AGENT_VISION:-Become a transparent digital coworker.}" <<'PY'
import asyncio
import sys

project_root, db_path, vision = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, project_root)

try:
    from core.cognitive_ledger import CognitiveLedger
except ImportError as exc:
    raise SystemExit(f"ERROR: cannot import core.cognitive_ledger ({exc}). Run 'uv sync'.")


async def seed() -> None:
    ledger = CognitiveLedger(db_path)
    await ledger.initialize()
    if not await ledger.list_core_directives():
        await ledger.create(
            "core_directives", directive_type="VISION", content=vision
        )
        await ledger.create(
            "core_directives",
            directive_type="IMMUTABLE_PERSONA",
            content="Calm, precise, and fully auditable. Never expose secrets.",
        )
        await ledger.create(
            "core_directives",
            directive_type="SYSTEM_ANATOMY",
            content="Immutable core under core/ and api/; capabilities live in plugins/.",
        )
    print(f"Genesis DB ready: {db_path}")


asyncio.run(seed())
PY

echo "Done. Container: $CONTAINER_NAME"
echo "  workspace -> $WORKSPACE_DIR (container: /mnt/workspace)"
echo "  atelier   -> $ATELIER_DIR (container: /mnt/agent_atelier)"
if [[ "$WITH_ANTIGRAVITY" == "1" ]]; then
  echo "  agy state -> $AGY_STATE_DIR (container: /home/agent/.config/agy)"
fi
echo "  database  -> $DB_PATH"
