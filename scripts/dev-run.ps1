<#
.SYNOPSIS
  以與 start-dev.ps1 相同的開發環境設定執行一次性的 Python 指令（.venv 的 python）。

.DESCRIPTION
  讀 .env.dev，設定 DATABASE_URL、KNOWLEDGE_DATABASE_URL、CLOUD_INTERNAL_URL／TOKEN（本機後端）、
  APP_ENV=development 等環境變數後執行，例如：
    .\scripts\dev-run.ps1 -m app.pricing_scheduler --run-once
    .\scripts\dev-run.ps1 -m knowledge_rag.admin status
  crawl --queued、index build、status 的佇列數需要後端已在執行（先跑 start-dev.ps1）。
#>
$Root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'dev-env.ps1')
$EnvFile = Join-Path $Root '.env.dev'
if (-not (Test-Path $EnvFile)) { Write-Host '錯誤：找不到 .env.dev，請先執行 scripts/start-dev.ps1。' -ForegroundColor Red; exit 1 }
$Py = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Py)) { Write-Host '錯誤：找不到 .venv，請先執行 scripts/start-dev.ps1。' -ForegroundColor Red; exit 1 }
Set-DevEnvironment (Read-DevConfig $EnvFile) $Root | Out-Null
Set-Location $Root
& $Py @args
exit $LASTEXITCODE
