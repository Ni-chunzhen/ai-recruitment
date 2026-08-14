$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BackendDir = Join-Path $ProjectRoot "backend"
$Python = Join-Path $BackendDir ".venv\Scripts\python.exe"
$PreviousLocation = Get-Location

try {
    if (-not (Test-Path $Python)) {
        throw "backend/.venv not found. Create the venv and install dependencies first."
    }

    Set-Location $BackendDir
    Write-Host "Starting Celery worker for AI tasks..." -ForegroundColor Cyan
    & $Python -m celery -A app.workers.celery_app.celery_app worker --loglevel=INFO --pool=solo
}
finally {
    Set-Location $PreviousLocation
}
