# Restart the VGAC backend cleanly: kill every uvicorn python process (a stale one can hold port 8765 while a new
# one silently fails to bind), then start one on 8765 with logs in runtime/logs/backend.log.
# First the start gate (vgac/move_gate.py): while a move of the big files is unfinished or unreadable nothing is killed or started.
Push-Location $PSScriptRoot
& ".\.venv\Scripts\python.exe" -m vgac.move_gate
$gate = $LASTEXITCODE
Pop-Location
if ($gate -ne 0) { exit 1 }
Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*uvicorn*vgac.main*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2
$root = Split-Path -Parent $PSScriptRoot
Start-Process -FilePath "$PSScriptRoot\.venv\Scripts\python.exe" -ArgumentList "-m","uvicorn","vgac.main:app","--host","127.0.0.1","--port","8765" -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput "$root\runtime\logs\backend.log" -RedirectStandardError "$root\runtime\logs\backend.err.log"
"backend starting on http://127.0.0.1:8765"
