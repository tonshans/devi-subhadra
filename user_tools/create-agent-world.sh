#!/usr/bin/env bash
# Create the LXC "agent world": UID/GID mapping, bind mounts, and Genesis DB seed.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
CONTAINER_NAME="${CONTAINER_NAME:-devi-subhadra}"
LXC_PATH="${LXC_PATH:-/var/lib/lxc}"
DISTRO="${DISTRO:-ubuntu}"
RELEASE="${RELEASE:-noble}"
ARCH="${ARCH:-amd64}"

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

echo "[1/5] Preparing host directories"
install -d -o "$HOST_UID" -g "$HOST_GID" -m 0755 \
  "$WORKSPACE_DIR" "$ATELIER_DIR" "$DATA_DIR"

echo "[2/5] Creating container '$CONTAINER_NAME' (if missing)"
if [[ ! -d "$LXC_PATH/$CONTAINER_NAME" ]]; then
  lxc-create -n "$CONTAINER_NAME" -t download -- \
    -d "$DISTRO" -r "$RELEASE" -a "$ARCH"
fi

echo "[3/5] Applying UID/GID mapping and bind mounts"
CONFIG_FILE="$LXC_PATH/$CONTAINER_NAME/config"
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
lxc.apparmor.profile = generated
# END devi-subhadra
EOF

echo "[4/5] Starting container"
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

echo "[5/5] Seeding Genesis DB at $DB_PATH"
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
echo "  database  -> $DB_PATH"