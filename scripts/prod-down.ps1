<#
.SYNOPSIS
  關閉 T17 正式環境（compose.prod.yaml）：停止容器、快速通道與主機上的 YOLO；資料保留，Ollama 不關。
#>
$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
docker compose --env-file .env.prod -f compose.yaml -f compose.prod.yaml --profile tunnel stop
$YoloPidFile = Join-Path $Root '.dev\prod-yolo.pid'
if (Test-Path $YoloPidFile) {
    Stop-Process -Id (Get-Content $YoloPidFile) -ErrorAction SilentlyContinue
    Remove-Item $YoloPidFile
}
Write-Host '正式環境已關閉（資料保留；下次 prod-up 會取得新的網址）。'
