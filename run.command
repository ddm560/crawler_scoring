#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$SCRIPT_DIR/.venv/bin/python3"

if [[ ! -x "$PYTHON" ]]; then
  echo "Error: Python virtual environment not found at $PYTHON"
  echo "Create it first with: python3.12 -m venv .venv"
  read -r -p "Press Enter to exit."
  exit 1
fi

cd "$SCRIPT_DIR"
"$PYTHON" app_cli.py
STATUS=$?

if [[ $STATUS -ne 0 ]]; then
  read -r -p "Application failed. Press Enter to exit."
fi

exit $STATUS
