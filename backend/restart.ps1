# Restart the VGAC backend cleanly: kill every uvicorn python process (a stale one can hold port 8765 while a new
# one silently fails to bind), then start one on 8765 with logs in runtime/logs/backend.log.
Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*uvicorn*vgac.main*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2
$root = Split-Path -Parent $PSScriptRoot
Start-Process -FilePath "$PSScriptRoot\.venv\Scripts\python.exe" -ArgumentList "-m","uvicorn","vgac.main:app","--host","127.0.0.1","--port","8765" -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput "$root\runtime\logs\backend.log" -RedirectStandardError "$root\runtime\logs\backend.err.log"
"backend starting on http://127.0.0.1:8765"
