#!/usr/bin/env bash
# Reset the demo. Removes ONLY MORPHE's own data: ./data/qdrant and the MongoDB database named in MONGODB_DATABASE.
# With the server running it goes through the API; with the server stopped use --offline.
set -euo pipefail
cd "$(dirname "$0")/../.."
exec .venv/bin/python backend/scripts/demo.py reset "$@"
