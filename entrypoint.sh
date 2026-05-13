#!/bin/sh
set -e

echo "=== Running database migrations ==="
alembic upgrade head

echo "=== Starting WS Router ==="
exec uvicorn src.main:app --host 0.0.0.0 --port 8060
