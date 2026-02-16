#!/usr/bin/env bash
# Kalshi BTC Bot — One-Click Setup (Mac/Linux)
# Usage: chmod +x run_me_first.sh && ./run_me_first.sh

set -e

# Change to the directory where this script lives
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo
echo "  ================================================================"
echo "    Kalshi BTC Bot — One-Click Setup"
echo "  ================================================================"
echo

# ── Check for Python 3.11+ ──────────────────────────────────────────
PYTHON_CMD=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        version=$("$cmd" --version 2>&1 | awk '{print $2}')
        major=$(echo "$version" | cut -d. -f1)
        minor=$(echo "$version" | cut -d. -f2)
        if [ "$major" -ge 3 ] && [ "$minor" -ge 11 ]; then
            PYTHON_CMD="$cmd"
            echo "  Found $cmd $version"
            break
        fi
    fi
done

if [ -z "$PYTHON_CMD" ]; then
    echo "  [ERROR] Python 3.11+ is required but not found."
    echo
    echo "  Install Python from: https://www.python.org/downloads/"
    echo "  On macOS with Homebrew: brew install python@3.12"
    echo "  On Ubuntu/Debian: sudo apt install python3.12 python3.12-venv"
    echo
    exit 1
fi

# ── Run the bootstrapper ────────────────────────────────────────────
"$PYTHON_CMD" one_click_setup_and_run.py
EXIT_CODE=$?

if [ $EXIT_CODE -ne 0 ]; then
    echo
    echo "  Setup exited with an error. See messages above."
    echo
fi

exit $EXIT_CODE
