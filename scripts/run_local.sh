#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

source .venv/bin/activate
pip install -r requirements.txt

export FLASK_DEBUG="${FLASK_DEBUG:-0}"
export DATABASE="${DATABASE:-predprof_case2.db}"
python3 app.py
