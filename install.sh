#!/usr/bin/env bash
# LoomLedge install: project-level dependencies only, then exit.
# Never uses sudo and never starts a server - run `loomledge` afterwards.
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"

echo "==> upgrading pip, setuptools and wheel"
"$PYTHON" -m pip install --upgrade pip setuptools wheel

echo "==> installing project dependencies (requirements.txt)"
"$PYTHON" -m pip install -r requirements.txt

echo "==> installing the loomledge command (editable)"
"$PYTHON" -m pip install -e .

echo "==> self-check"
"$PYTHON" -c 'import loomledge; assert callable(loomledge.main)'
"$PYTHON" -m loomledge --version

echo
echo "Setup complete. Nothing was started; this script has exited."
echo "Try:      python3 -m loomledge seed --data ./studio.json"
echo "          python3 -m loomledge operator login cenius --data ./studio.json"
echo "          python3 -m loomledge loom list --data ./studio.json"
echo "Demo login: operator cenius / passphrase cenius"
