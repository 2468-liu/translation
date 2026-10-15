#!/usr/bin/env bash
# One-click Nemotron ASR Setup Script for Linux / Ubuntu 24.04
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "=== NVIDIA Nemotron 3.5 ASR Linux One-Click Setup ==="

# Check python
if command -v python3 &>/dev/null; then
    PYTHON_CMD="python3"
elif command -v python &>/dev/null; then
    PYTHON_CMD="python"
else
    echo "Error: python3 is not installed. Please install Python 3.10+ first."
    exit 1
fi

cd "$PROJECT_ROOT"
"$PYTHON_CMD" "$SCRIPT_DIR/setup_nemotron.py" "$@"
