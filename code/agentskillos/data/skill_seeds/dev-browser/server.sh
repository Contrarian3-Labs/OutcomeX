#!/bin/bash

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Change to the script directory
cd "$SCRIPT_DIR"

# Parse command line arguments
HEADLESS=false
while [[ "$#" -gt 0 ]]; do
    case $1 in
        --headless) HEADLESS=true ;;
        *) echo "Unknown parameter: $1"; exit 1 ;;
    esac
    shift
done

LOG_DIR="$SCRIPT_DIR/tmp"
LOG_FILE="$LOG_DIR/dev-browser-server.log"
mkdir -p "$LOG_DIR"

if curl -fsS http://localhost:9222 >/dev/null 2>&1; then
    echo "Dev-browser server already running."
    echo "Ready"
    exit 0
fi

echo "Installing dependencies..."
npm install >/dev/null 2>&1

echo "Starting dev-browser server..."
HEADLESS=$HEADLESS nohup npx tsx scripts/start-server.ts >"$LOG_FILE" 2>&1 &
SERVER_PID=$!

for _ in $(seq 1 45); do
    if curl -fsS http://localhost:9222 >/dev/null 2>&1; then
        echo "Dev-browser server started (PID: $SERVER_PID)."
        echo "Log file: $LOG_FILE"
        echo "Ready"
        exit 0
    fi
    sleep 1
done

echo "Dev-browser server failed to become ready. Recent logs:"
tail -n 80 "$LOG_FILE" || true
exit 1
