#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$SCRIPT_DIR/.venv/bin/python3"

if [[ ! -x "$PYTHON" ]]; then
  echo "Error: Python virtual environment not found at $PYTHON"
  echo "Create it first with: python3.12 -m venv .venv"
  exit 1
fi

cd "$SCRIPT_DIR"
"$PYTHON" -m PyInstaller domains_scorer.spec

echo "Build complete: dist/domains_scorer"
