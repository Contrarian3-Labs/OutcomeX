#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GIT_COMMON_DIR="$(cd "$ROOT_DIR" && realpath "$(git rev-parse --git-common-dir)")"
OUTCOMEX_REPO_ROOT="$(dirname "$GIT_COMMON_DIR")"
WORKSPACE_ROOT="$(dirname "$OUTCOMEX_REPO_ROOT")"
BACKEND_DIR="$ROOT_DIR/code/backend"
CONTRACTS_DIR="$ROOT_DIR/code/contracts"
AGENTSKILLOS_DIR="$ROOT_DIR/code/agentskillos"
FRONTEND_DIR="${OUTCOMEX_FRONTEND_DIR:-$WORKSPACE_ROOT/hashkey/forge-yield-ai}"
STATE_DIR="$ROOT_DIR/.local/browser-demo"
LOG_DIR="$STATE_DIR/logs"
PID_DIR="$STATE_DIR/pids"
ANVIL_LOG="$LOG_DIR/anvil.log"
BACKEND_LOG="$LOG_DIR/backend.log"
FRONTEND_LOG="$LOG_DIR/frontend.log"
ANVIL_PID_FILE="$PID_DIR/anvil.pid"
BACKEND_PID_FILE="$PID_DIR/backend.pid"
FRONTEND_PID_FILE="$PID_DIR/frontend.pid"
PREPARE_ONLY=0

usage() {
  cat <<EOF
Usage: $(basename "$0") [--prepare-only]

Starts a deterministic local browser demo stack for OutcomeX:
- fresh Anvil chain on 127.0.0.1:8545
- local contract deployment
- three demo owner wallets funded with HSK/PWR/USDT + machine mint seed
- backend on 127.0.0.1:8787
- frontend on 127.0.0.1:8080

Options:
  --prepare-only   Only prepare chain + seed data; do not start backend/frontend
EOF
}

for arg in "$@"; do
  case "$arg" in
    --prepare-only) PREPARE_ONLY=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $arg" >&2; usage; exit 1 ;;
  esac
done

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

port_open() {
  python3 - "$1" <<'PY'
import socket, sys
port = int(sys.argv[1])
with socket.socket() as sock:
    sock.settimeout(0.25)
    sys.exit(0 if sock.connect_ex(("127.0.0.1", port)) == 0 else 1)
PY
}

wait_for_http() {
  local url="$1"
  local name="$2"
  for _ in $(seq 1 60); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  echo "$name did not become ready: $url" >&2
  exit 1
}

wait_for_rpc() {
  for _ in $(seq 1 40); do
    if curl -fsS -H 'Content-Type: application/json' \
      -d '{"jsonrpc":"2.0","method":"eth_chainId","params":[],"id":1}' \
      http://127.0.0.1:8545 >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.5
  done
  echo "Anvil RPC did not become ready" >&2
  exit 1
}

stop_pid_file() {
  local pid_file="$1"
  if [[ -f "$pid_file" ]]; then
    local pid
    pid="$(cat "$pid_file")"
    if kill -0 "$pid" >/dev/null 2>&1; then
      kill "$pid" >/dev/null 2>&1 || true
      wait "$pid" 2>/dev/null || true
    fi
    rm -f "$pid_file"
  fi
}

require_cmd python3
require_cmd curl
require_cmd anvil
require_cmd forge
require_cmd npm
require_cmd setsid

if [[ ! -d "$FRONTEND_DIR" ]]; then
  echo "Frontend repo not found: $FRONTEND_DIR" >&2
  exit 1
fi
if [[ ! -f "$AGENTSKILLOS_DIR/run.py" ]]; then
  echo "Vendored AgentSkillOS repo not found: $AGENTSKILLOS_DIR" >&2
  exit 1
fi
if [[ ! -x "$BACKEND_DIR/.venv/bin/python" ]]; then
  echo "Backend venv missing: $BACKEND_DIR/.venv/bin/python" >&2
  exit 1
fi
if [[ ! -f "$BACKEND_DIR/.env" ]]; then
  echo "Backend .env missing. Copy code/backend/.env.local-demo.example to code/backend/.env first." >&2
  exit 1
fi
if [[ ! -f "$FRONTEND_DIR/.env.local" ]]; then
  cp "$FRONTEND_DIR/.env.example" "$FRONTEND_DIR/.env.local"
fi

resolve_agentskillos_python() {
  if [[ -x "$AGENTSKILLOS_DIR/.venv/bin/python" ]]; then
    printf '%s' "$AGENTSKILLOS_DIR/.venv/bin/python"
    return 0
  fi
  if [[ -x "$AGENTSKILLOS_DIR/.venv/Scripts/python.exe" ]]; then
    printf '%s' "$AGENTSKILLOS_DIR/.venv/Scripts/python.exe"
    return 0
  fi
  return 1
}

resolve_backend_sqlite_path() {
  (
    cd "$BACKEND_DIR"
    .venv/bin/python - <<'PY'
from app.core.config import get_settings
from pathlib import Path

url = get_settings().database_url
prefix = "sqlite+pysqlite:///"
if url.startswith(prefix):
    sqlite_path = Path(url[len(prefix):])
    if not sqlite_path.is_absolute():
        sqlite_path = (Path.cwd() / sqlite_path).resolve()
    print(sqlite_path)
PY
  )
}

mkdir -p "$LOG_DIR" "$PID_DIR"

stop_pid_file "$BACKEND_PID_FILE"
stop_pid_file "$FRONTEND_PID_FILE"
stop_pid_file "$ANVIL_PID_FILE"

if port_open 8787; then
  echo "Port 8787 is already in use by another process. Stop it first." >&2
  exit 1
fi
if port_open 8080; then
  echo "Port 8080 is already in use by another process. Stop it first." >&2
  exit 1
fi
if port_open 8545; then
  echo "Port 8545 is already in use by another process. Stop it first so the demo can start from a clean deterministic chain." >&2
  exit 1
fi

SQLITE_DB_PATH="$(resolve_backend_sqlite_path)"
if [[ -n "$SQLITE_DB_PATH" ]]; then
  rm -f "$SQLITE_DB_PATH"
fi
rm -rf "$BACKEND_DIR/data/agentskillos-execution"

nohup setsid anvil --host 127.0.0.1 --port 8545 --chain-id 133 >"$ANVIL_LOG" 2>&1 < /dev/null &
echo $! >"$ANVIL_PID_FILE"
wait_for_rpc

(
  cd "$CONTRACTS_DIR"
  forge script script/DeployLocal.s.sol:DeployLocal \
    --rpc-url http://127.0.0.1:8545 \
    --private-key 0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80 \
    --broadcast \
    -q
) >>"$ANVIL_LOG" 2>&1

(
  cd "$BACKEND_DIR"
  .venv/bin/python scripts/prepare_local_browser_demo.py
) | tee -a "$ANVIL_LOG"

if [[ "$PREPARE_ONLY" -eq 1 ]]; then
  echo "Prepared chain + demo seed only. Anvil is running on http://127.0.0.1:8545"
  exit 0
fi

AGENTSKILLOS_PYTHON=""
BACKEND_ENV=(OUTCOMEX_AGENTSKILLOS_ROOT="$AGENTSKILLOS_DIR")
if AGENTSKILLOS_PYTHON="$(resolve_agentskillos_python)"; then
  echo "Using vendored AgentSkillOS python: $AGENTSKILLOS_PYTHON" >>"$BACKEND_LOG"
  BACKEND_ENV+=(OUTCOMEX_AGENTSKILLOS_PYTHON_EXECUTABLE="$AGENTSKILLOS_PYTHON")
fi

nohup setsid env   "${BACKEND_ENV[@]}"   "$BACKEND_DIR/.venv/bin/python" -m uvicorn app.main:app --host 127.0.0.1 --port 8787 >"$BACKEND_LOG" 2>&1 < /dev/null &
echo $! >"$BACKEND_PID_FILE"
wait_for_http "http://127.0.0.1:8787/api/v1/health" "Backend"

nohup setsid bash -lc "cd '$FRONTEND_DIR' && npm run dev -- --host 127.0.0.1 --port 8080" >"$FRONTEND_LOG" 2>&1 < /dev/null &
echo $! >"$FRONTEND_PID_FILE"
wait_for_http "http://127.0.0.1:8080" "Frontend"

cat <<EOF
Local browser demo is ready.

Manual testing checklist:
- `buyer`, `owner-1`, and `owner-2` wallets are each pre-funded with 10 HSK, 10,000 PWR, and 100 USDT; use `buyer` to buy the seeded machines.
- Owner-1 owns machine-owner-1 and the machine stays unlisted so you can test listing creation and delisting flows.
- Owner-2 owns two machines with active onchain secondary listings seeded in USDT during startup.
- Primary issuance stock is seeded to 10 so primary issuance flows remain available after seeding.

Seed summary:
- Demo wallets:
  - buyer (10 HSK / 10,000 PWR / 100 USDT, wallet 0xd9180752dfdC003Fa5bD2a4bb9b0Ead2E2149CdB)
  - owner-1 (10 HSK / 10,000 PWR / 100 USDT, wallet 0x0A4401376B024E72cA9481192c88F4d4eb80cDf8)
  - owner-2 (10 HSK / 10,000 PWR / 100 USDT, wallet 0x1feDb8e927b9A1c9878c8C9e0beA518Fc96A9265)
- Owners:
  - owner-1 → machine-owner-1 (unlisted)
  - owner-2 → machine-owner-2 (active USDT listing, 1,250,000 units)
  - owner-2 → machine-owner-3 (active USDT listing, 1,550,000 units)
- Primary issuance stock: 10

URLs:
- Frontend: http://127.0.0.1:8080
- Backend:  http://127.0.0.1:8787/api/v1/health
- Anvil:    http://127.0.0.1:8545

Suggested wallets on Anvil:
- buyer:    0xd9180752dfdC003Fa5bD2a4bb9b0Ead2E2149CdB
- owner-1:  0x0A4401376B024E72cA9481192c88F4d4eb80cDf8
- owner-2:  0x1feDb8e927b9A1c9878c8C9e0beA518Fc96A9265


Logs:
- $ANVIL_LOG
- $BACKEND_LOG
- $FRONTEND_LOG

Stop everything with:
  $ROOT_DIR/scripts/stop_local_browser_demo.sh
EOF
