#!/usr/bin/env bash
# Sales Process Audit — start the app on this machine (macOS / Linux).
#   chmod +x start_audit.sh   (once)
#   ./start_audit.sh
# The Windows equivalent is START_AUDIT.bat — double-click it.
set -e
cd "$(dirname "$0")"

PY=$(command -v python3 || command -v python || true)
if [ -z "$PY" ]; then
  echo "Python 3 is not installed. Install it from https://www.python.org/downloads/"
  exit 1
fi

if [ ! -x ".venv/bin/python" ]; then
  echo "First run — setting up. This takes a minute or two."
  "$PY" -m venv .venv
fi

.venv/bin/python -m pip install --upgrade pip --quiet --disable-pip-version-check
.venv/bin/python -m pip install -r requirements.txt --quiet --disable-pip-version-check

# Keep the phone captures and the run history beside the project rather than
# in a temp folder that the OS may clear.
mkdir -p output/physical
export AUDIT_PHYSICAL_DIR="$PWD/output/physical"
export AUDIT_HISTORY_DIR="$PWD/output"

exec .venv/bin/python webapp/app.py
