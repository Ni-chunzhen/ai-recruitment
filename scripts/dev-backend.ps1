$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BackendDir = Join-Path $ProjectRoot "backend"
$Python = Join-Path $BackendDir ".venv\Scripts\python.exe"
$PreviousLocation = Get-Location

try {
    if (-not (Test-Path $Python)) {
        throw "未找到 backend/.venv，请先在 backend 目录创建虚拟环境并安装依赖。"
    }

    Set-Location $BackendDir
    & $Python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
}
finally {
    Set-Location $PreviousLocation
}