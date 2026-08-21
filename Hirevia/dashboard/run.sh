#!/usr/bin/env bash
# Run the Hirevia Dashboard on http://localhost:3000
set -euo pipefail
cd "$(dirname "$0")"

# Create venv if missing
if [ ! -d .venv ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv .venv
    .venv/bin/pip install -q -r requirements.txt -r ../requirements.txt
fi

echo "🎯 Starting Hirevia Dashboard on http://localhost:3000"
exec .venv/bin/python -m uvicorn app:app --host 0.0.0.0 --port 3000 --reload
