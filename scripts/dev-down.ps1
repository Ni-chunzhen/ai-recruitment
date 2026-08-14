$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ComposeFile = Join-Path $ProjectRoot "infra\compose.yaml"
$EnvFile = Join-Path $ProjectRoot ".env"

docker compose `
    --env-file $EnvFile `
    -f $ComposeFile `
    down

if ($LASTEXITCODE -ne 0) {
    throw "基础设施停止失败。"
}

Write-Host "基础设施已停止，数据卷已保留。" `
    -ForegroundColor Green