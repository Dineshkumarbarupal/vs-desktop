#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3.10+ is required." >&2
  exit 1
fi
if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
fi
.venv/bin/python -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)' || { echo "FlowCode Studio requires Python 3.10 or newer." >&2; exit 1; }
needs_install=0
if [[ ! -f .venv/.flowcode-deps-version ]] || [[ "$(cat .venv/.flowcode-deps-version)" != "0.25.0" ]]; then
  needs_install=1
fi
if ! .venv/bin/python -c 'import eel, jedi, black, websocket' >/dev/null 2>&1; then
  needs_install=1
fi
if [[ "$needs_install" == "1" ]]; then
  .venv/bin/python -m pip install --disable-pip-version-check -r requirements.txt
  printf '0.25.0' > .venv/.flowcode-deps-version
fi
exec .venv/bin/python main.py
