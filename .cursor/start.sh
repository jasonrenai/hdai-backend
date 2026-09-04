#!/usr/bin/env bash
# Per-boot startup: ensure a local MongoDB is running and ready.
# Idempotent — safe to run on every environment start.
set -euo pipefail

sudo mkdir -p /data/db /var/log/mongodb
sudo chown -R "$(id -u):$(id -g)" /data/db /var/log/mongodb 2>/dev/null || true

if pgrep -x mongod >/dev/null 2>&1; then
  echo ">> mongod already running."
else
  echo ">> Starting mongod..."
  mongod --dbpath /data/db \
    --bind_ip 127.0.0.1 --port 27017 \
    --logpath /var/log/mongodb/mongod.log --fork
fi

# Wait for the server to accept connections.
for _ in $(seq 1 30); do
  if (exec 3<>/dev/tcp/127.0.0.1/27017) 2>/dev/null; then
    exec 3>&-
    echo ">> mongod is ready on 127.0.0.1:27017."
    exit 0
  fi
  sleep 1
done

echo "!! mongod did not become ready in time; see /var/log/mongodb/mongod.log" >&2
exit 1
