#!/usr/bin/env bash
# Starts a project-local MongoDB (data in ./data/mongo). Uses `mongod` from PATH,
# falling back to the copy unpacked in ./.tools/mongodb/bin.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MONGOD="$(command -v mongod || true)"
[ -z "$MONGOD" ] && MONGOD="$ROOT/.tools/mongodb/bin/mongod"
[ -x "$MONGOD" ] || { echo "mongod not found. Install MongoDB Community or unpack it to .tools/mongodb (see README)."; exit 1; }
mkdir -p "$ROOT/data/mongo"
exec "$MONGOD" --dbpath "$ROOT/data/mongo" --bind_ip 127.0.0.1 --port 27017
