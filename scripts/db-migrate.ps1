$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BackendDir = Join-Path $ProjectRoot "backend"
$Python = Join-Path $BackendDir ".venv\Scripts\python.exe"
$EnvFile = Join-Path $BackendDir ".env"
$PreviousLocation = Get-Location

try {
    if (-not (Test-Path $Python)) {
        throw "未找到 backend/.venv，请先在 backend 目录创建虚拟环境并安装依赖。"
    }

    if (-not (Test-Path $EnvFile)) {
        throw "未找到 backend/.env，请先从 backend/.env.example 复制并配置。"
    }

    Set-Location $BackendDir
    & $Python -m alembic upgrade head

    if ($LASTEXITCODE -ne 0) {
        throw "数据库迁移失败。"
    }

    Write-Host "数据库迁移完成。" -ForegroundColor Green
}
finally {
    Set-Location $PreviousLocation
}