#!/usr/bin/env sh
# Start the Video Game Asset Creator backend on port 8765 with auto-reload.
cd "$(dirname "$0")" || exit 1
if [ -x .venv/Scripts/python.exe ]; then
  PY=.venv/Scripts/python.exe
else
  PY=.venv/bin/python
fi
# The start gate (vgac/move_gate.py): refuse while a move of the big files is unfinished or unreadable.
"$PY" -m vgac.move_gate || exit 1
exec "$PY" -m uvicorn vgac.main:app --host 127.0.0.1 --port 8765 --reload
