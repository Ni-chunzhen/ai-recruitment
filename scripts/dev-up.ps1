$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ComposeFile = Join-Path $ProjectRoot "infra\compose.yaml"
$EnvFile = Join-Path $ProjectRoot ".env"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "未找到 Docker 命令，请先安装并启动 Docker Desktop。"
}

docker info *> $null

if ($LASTEXITCODE -ne 0) {
    throw "Docker Desktop 当前未运行，请启动后重试。"
}

if (-not (Test-Path $EnvFile)) {
    Copy-Item `
        (Join-Path $ProjectRoot ".env.example") `
        $EnvFile

    Write-Host "已创建 .env，请先修改其中的本地密码。" `
        -ForegroundColor Yellow
    exit 1
}

docker compose `
    --env-file $EnvFile `
    -f $ComposeFile `
    up -d

if ($LASTEXITCODE -ne 0) {
    throw "基础设施启动失败。"
}

Write-Host ""
Write-Host "基础设施启动完成：" -ForegroundColor Green
Write-Host "PostgreSQL: localhost:5432"
Write-Host "Redis:      localhost:6379"
Write-Host "MinIO API:  http://localhost:9000"
Write-Host "MinIO 控制台: http://localhost:9001"
Write-Host ""
Write-Host "执行 .\scripts\dev-status.ps1 查看健康状态。"