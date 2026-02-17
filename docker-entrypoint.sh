#!/bin/bash
set -e

echo "=== OFM Starting ==="
echo "DB_HOST=${DB_HOST:-not set}"
echo "DB_PORT=${DB_PORT:-not set}"

# Start Flask backend (legacy dashboard)
echo "Starting Flask backend on :8501..."
python app.py &
PID1=$!

# Start FastAPI backend (API for Next.js frontend)
echo "Starting FastAPI backend on :8000..."
python -m uvicorn api:app --host 0.0.0.0 --port 8000 &
PID2=$!

# Start Next.js frontend
echo "Starting Next.js frontend on :3000..."
cd /app/frontend && npx next start -p 3000 &
PID3=$!

echo "All services started (Flask=$PID1 FastAPI=$PID2 Next=$PID3)"

# Wait forever
wait
