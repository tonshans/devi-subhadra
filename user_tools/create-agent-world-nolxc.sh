#!/usr/bin/env bash
# Create the agent world directly on this (dedicated) host: data dirs, Playwright setup, config patch, and Genesis DB seed.
# No LXC: the agent runs as the invoking user, so no UID mapping, bind mounts, or root are needed.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"

WITH_ANTIGRAVITY="${WITH_ANTIGRAVITY:-0}"
AGY_INSTALL_URL="${AGY_INSTALL_URL:-}"

ARG_COUNT=$#
while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-antigravity) WITH_ANTIGRAVITY=1 ;;
    --help|-h)
      echo "Usage: $0 [--with-antigravity]"
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

DATA_DIR="${DATA_DIR:-$PROJECT_ROOT/data}"
WORKSPACE_DIR="${WORKSPACE_DIR:-$DATA_DIR/workspace}"
ATELIER_DIR="${ATELIER_DIR:-$DATA_DIR/agent_atelier}"
DB_PATH="${DB_PATH:-$DATA_DIR/devi_subhadra.db}"
CONFIG_FILE="$PROJECT_ROOT/config.yaml"

PYTHON_BIN="${PYTHON_BIN:-$PROJECT_ROOT/.venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then PYTHON_BIN="$(command -v python3 || true)"; fi
if [[ -z "$PYTHON_BIN" ]]; then
  echo "ERROR: python3 not found. Install Python 3.11+ first." >&2
  exit 1
fi

# Agent-owned files must belong to the normal user, not to root via sudo.
if [[ "$EUID" -eq 0 ]]; then
  echo "ERROR: run as your normal user (not sudo/root); agent files must be user-owned." >&2
  exit 1
fi

echo "[1/5] Preparing data directories"
install -d -m 0755 "$DATA_DIR" "$WORKSPACE_DIR" "$ATELIER_DIR"

echo "[2/5] Pointing config.yaml at local mode and data/ paths"
if [[ -f "$CONFIG_FILE" ]]; then
  # One key per line; keeps the rest of the file and its comments intact.
  sed -i -E 's|^([[:space:]]*execution_mode:).*|\1 "local"|' "$CONFIG_FILE"
  sed -i -E 's|^([[:space:]]*workspace_dir:).*|\1 "./data/workspace"|' "$CONFIG_FILE"
  sed -i -E 's|^([[:space:]]*atelier_dir:).*|\1 "./data/agent_atelier"|' "$CONFIG_FILE"
  sed -i -E 's|^([[:space:]]*halt_dir:).*|\1 "./data/workspace"|' "$CONFIG_FILE"
else
  echo "WARNING: $CONFIG_FILE not found; set these manually:" >&2
  echo "  execution_mode: \"local\"" >&2
  echo "  paths.workspace_dir: \"./data/workspace\"" >&2
  echo "  paths.atelier_dir: \"./data/agent_atelier\"" >&2
  echo "  halt_dir: \"./data/workspace\"" >&2
fi

echo "[3/5] Configuring Antigravity CLI (agy)"
if [[ "$WITH_ANTIGRAVITY" == "1" ]]; then
  if ! command -v agy >/dev/null 2>&1; then
    if [[ -z "$AGY_INSTALL_URL" ]]; then
      echo "WARNING: AGY_INSTALL_URL is not set; agy was not installed." >&2
    else
      curl -fsSL "$AGY_INSTALL_URL" | bash
    fi
  fi
  install -d -m 0700 "$HOME/.config/agy"
else
  echo "  skipped (pass --with-antigravity to enable)"
fi

echo "[4/5] Installing Playwright Chromium browser"
if ! "$PYTHON_BIN" -m playwright install chromium; then
  echo "WARNING: Failed to install Playwright browser. Did you run 'uv sync' first?" >&2
fi

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

echo "Done. Agent world on this machine (local mode, no container)."
echo "  workspace -> $WORKSPACE_DIR"
echo "  atelier   -> $ATELIER_DIR"
if [[ "$WITH_ANTIGRAVITY" == "1" ]]; then
  echo "  agy state -> $HOME/.config/agy"
fi
echo "  database  -> $DB_PATH"
echo "  WARNING: no container isolation; the agent can reach anything this user can."
