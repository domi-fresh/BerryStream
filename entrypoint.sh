#!/bin/sh
set -e

cleanup() {
  echo "Shutting down Canopi services..."
  kill $(jobs -p) 2>/dev/null || true
  exit 0
}
trap cleanup SIGINT SIGTERM EXIT

echo "Starting go2rtc core..."
go2rtc -config /app/go2rtc.yaml &

echo "Starting web server on port 8000..."
python3 -m http.server 8000 --directory /app/web &

wait
