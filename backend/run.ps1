# Start the Video Game Asset Creator backend on port 8765 with auto-reload.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here
# The start gate (vgac/move_gate.py): refuse while a move of the big files is unfinished or unreadable.
& "$here\.venv\Scripts\python.exe" -m vgac.move_gate
if ($LASTEXITCODE -ne 0) { exit 1 }
& "$here\.venv\Scripts\python.exe" -m uvicorn vgac.main:app --host 127.0.0.1 --port 8765 --reload
