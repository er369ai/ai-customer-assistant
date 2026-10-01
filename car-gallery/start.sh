#!/bin/sh
# Starts the assistant and opens it in the browser. The first run installs the Claude library (~1 min).
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
  echo "First run: installing the Claude library..."
  python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt || exit 1
fi
PORT=$(.venv/bin/python -c 'import json; print(json.load(open("business.json"))["port"])')
(sleep 1; xdg-open "http://localhost:$PORT" >/dev/null 2>&1) &
exec .venv/bin/python app.py
