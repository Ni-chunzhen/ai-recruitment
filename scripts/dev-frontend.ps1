$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$FrontendDir = Join-Path $ProjectRoot "frontend"
$PreviousLocation = Get-Location

if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) {
    throw "未找到 pnpm 命令，请先安装 Node.js LTS 和 pnpm。"
}

try {
    Set-Location $FrontendDir
    pnpm run dev
}
finally {
    Set-Location $PreviousLocation
}