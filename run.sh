#!/usr/bin/env bash
# Start ImgMetaManager on Linux and macOS.
# Creates the virtual environment and installs the dependencies on first run.
set -euo pipefail

cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
VENV=".venv"

if ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "Python 3.10 or newer is required. Install it, then run this script again." >&2
    exit 1
fi

if [ ! -d "$VENV" ]; then
    echo "-> Creating the virtual environment..."
    "$PYTHON" -m venv "$VENV"
    "$VENV/bin/python" -m pip install --upgrade pip >/dev/null
    echo "-> Installing dependencies (first run only)..."
    "$VENV/bin/python" -m pip install -r requirements.txt
fi

exec "$VENV/bin/python" -m imgmetamanager "$@"
