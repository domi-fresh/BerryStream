#!/usr/bin/env bash
set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

cleanup() {
  echo ""
  echo "Shutting down EVF services..."
  kill $(jobs -p) 2>/dev/null || true
  exit 0
}
trap cleanup SIGINT SIGTERM EXIT

# Kill lingering instances
sudo pkill -9 go2rtc 2>/dev/null || true
sudo fuser -k 8000/tcp 2>/dev/null || true

echo "Starting go2rtc streaming core..."
/home/domi/go2rtc -config "$PROJECT_DIR/go2rtc.yaml" &

echo "Starting local web interface on port 8000..."
python3 -m http.server 8000 --directory "$PROJECT_DIR/web" &

echo "EVF ready. Open on iPhone: http://172.20.10.2:8000"
wait
