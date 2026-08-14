$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ComposeFile = Join-Path $ProjectRoot "infra\compose.yaml"
$EnvFile = Join-Path $ProjectRoot ".env"

docker compose `
    --env-file $EnvFile `
    -f $ComposeFile `
    ps

Write-Host ""
Write-Host "服务连接检查：" -ForegroundColor Cyan

docker exec ai-recruit-postgres `
    pg_isready -U recruit -d recruit

docker exec ai-recruit-redis `
    redis-cli ping

$MinioStatus = Invoke-WebRequest `
    -Uri "http://localhost:9000/minio/health/live" `
    -UseBasicParsing

if ($MinioStatus.StatusCode -eq 200) {
    Write-Host "MinIO: OK" -ForegroundColor Green
}

Write-Host ""
Write-Host "API 健康检查：" -ForegroundColor Cyan

try {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/health" -TimeoutSec 3
    Write-Host "/api/v1/health: OK (code=$($health.code))" -ForegroundColor Green
} catch {
    Write-Host "/api/v1/health: 不可用（后端未启动？）" -ForegroundColor Yellow
}

try {
    $live = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/health/live" -TimeoutSec 3
    Write-Host "/api/v1/health/live: OK (status=$($live.data.status))" -ForegroundColor Green
} catch {
    Write-Host "/api/v1/health/live: 不可用" -ForegroundColor Yellow
}

try {
    $ready = Invoke-WebRequest `
        -Uri "http://127.0.0.1:8000/api/v1/health/ready" `
        -UseBasicParsing `
        -TimeoutSec 3
    $readyBody = $ready.Content | ConvertFrom-Json
    $pgStatus = $readyBody.data.checks.postgresql
    $redisStatus = $readyBody.data.checks.redis
    $color = if ($ready.StatusCode -eq 200) { "Green" } else { "Yellow" }
    Write-Host "/api/v1/health/ready: HTTP $($ready.StatusCode) (postgresql=$pgStatus, redis=$redisStatus)" `
        -ForegroundColor $color
} catch {
    $statusCode = $_.Exception.Response.StatusCode.value__
    if ($statusCode) {
        $reader = [System.IO.StreamReader]::new($_.Exception.Response.GetResponseStream())
        $readyBody = $reader.ReadToEnd() | ConvertFrom-Json
        $pgStatus = $readyBody.data.checks.postgresql
        $redisStatus = $readyBody.data.checks.redis
        Write-Host "/api/v1/health/ready: HTTP $statusCode (postgresql=$pgStatus, redis=$redisStatus)" `
            -ForegroundColor Yellow
    } else {
        Write-Host "/api/v1/health/ready: 不可用" -ForegroundColor Yellow
    }
}
