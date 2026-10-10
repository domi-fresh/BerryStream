#!/bin/sh
set -e

cleanup() {
  echo "Shutting down BerryStream services..."
  kill $(jobs -p) 2>/dev/null || true
  exit 0
}
trap cleanup SIGINT SIGTERM EXIT

echo "Starting go2rtc core..."
go2rtc -config /app/go2rtc.yaml &

echo "Starting BerryStream controller & web UI..."
python3 /app/server.py &

wait
