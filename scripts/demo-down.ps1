<#
.SYNOPSIS
  關閉 scripts/demo-up.ps1 啟動的單機 demo（全部容器、cloudflared 快速通道、主機上的 YOLO）。資料保留在 volume。

.PARAMETER Purge
  連同 demo 的資料 volume（overcooked-demo_*：資料庫、知識庫、上傳檔）一起刪除；會要求輸入確認。
  不會動到開發用的 overcooked-dev-db 與其 volume。
#>
param([switch]$Purge)

$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$YoloPidFile = Join-Path $Root '.dev\demo-yolo.pid'
if (Test-Path $YoloPidFile) {
    $procId = Get-Content $YoloPidFile
    if (Get-Process -Id $procId -ErrorAction SilentlyContinue) {
        taskkill /T /F /PID $procId *> $null
        Write-Host '已關閉 YOLO'
    }
    Remove-Item $YoloPidFile
}

if (-not (Test-Path (Join-Path $Root '.env.demo'))) { Write-Host '沒有 .env.demo，demo 沒有啟動過。'; exit 0 }
$Compose = @('compose', '-p', 'overcooked-demo', '--env-file', '.env.demo', '-f', 'compose.yaml', '-f', 'compose.demo.yaml',
    '--profile', 'tunnel', '--profile', 'tools')

if ($Purge) {
    $answer = Read-Host '確定要刪除 demo 的所有資料（資料庫、知識庫、上傳檔）？輸入 PURGE 確認'
    if ($answer -cne 'PURGE') { Write-Host '已取消，未刪除任何資料。'; $Purge = $false }
}
if ($Purge) {
    docker @Compose down --remove-orphans --volumes
    if ($LASTEXITCODE -eq 0) { Write-Host 'demo 已關閉，資料 volume 已刪除（.env.demo 保留，下次會用同一組密碼重建）。' -ForegroundColor Yellow }
} else {
    docker @Compose down --remove-orphans
    if ($LASTEXITCODE -eq 0) { Write-Host 'demo 已關閉，對外網址已失效（資料保留）。' -ForegroundColor Green }
}
