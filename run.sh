#!/usr/bin/env bash
# Start the app.
#
#   ./run.sh          local development: python app.py (debug, auto-reload,
#                     scheduler runs inside the app) on http://localhost:5000
#   ./run.sh prod     gunicorn, as in the Dockerfile; the scheduler is not
#                     started here - run scripts/run_scheduler.py on its own
#
# Stop it with Ctrl+C (Ctrl+Z only suspends it, and it keeps port 5000).
#
# Quick check once it is up:
#   curl -X POST http://localhost:5000/api/generate \
#     -H "Content-Type: application/json" \
#     -d '{"story":"Launching our AI tool...","platforms":["facebook","instagram"]}'
set -euo pipefail

cd "$(dirname "$0")"

if [ -x .venv/bin/python ]; then
    PYTHON=.venv/bin/python
else
    PYTHON=python3
fi

if lsof -nP -iTCP:5000 -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Port 5000 is already in use - is the app already running (or suspended with Ctrl+Z)?" >&2
    lsof -nP -iTCP:5000 -sTCP:LISTEN >&2
    exit 1
fi

case "${1:-dev}" in
    dev)
        exec "$PYTHON" app.py
        ;;
    prod)
        SCHEDULER_ENABLED=false exec "$PYTHON" -m gunicorn --worker-class gthread --threads 4 \
            --timeout 1900 --graceful-timeout 60 --bind 0.0.0.0:5000 --config gunicorn.conf.py \
            'app:create_app("production")'
        ;;
    *)
        echo "Usage: ./run.sh [dev|prod]" >&2
        exit 2
        ;;
esac
