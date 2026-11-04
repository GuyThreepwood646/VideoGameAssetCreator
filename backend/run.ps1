# Start the Video Game Asset Creator backend on port 8765 with auto-reload.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here
& "$here\.venv\Scripts\python.exe" -m uvicorn vgac.main:app --host 127.0.0.1 --port 8765 --reload
