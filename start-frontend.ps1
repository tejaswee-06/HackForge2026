# ASTRA frontend (Windows PowerShell): dev server on http://localhost:5173 proxying /api and /health to the backend
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "frontend")
if (-not (Test-Path "node_modules")) { npm install }
npm run dev
