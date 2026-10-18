# ASTRA backend (Windows PowerShell). Creates backend\.venv on first run, then serves http://127.0.0.1:8000
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "backend")
if (-not (Test-Path ".venv")) {
  py -3 -m venv .venv
  .\.venv\Scripts\python.exe -m pip install --upgrade pip
  .\.venv\Scripts\pip.exe install -r requirements-dev.txt
}
$envArgs = @()
if (Test-Path "..\.env") { $envArgs = @("--env-file", "..\.env") }
$port = if ($env:ASTRA_PORT) { $env:ASTRA_PORT } else { "8000" }
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port $port @envArgs
