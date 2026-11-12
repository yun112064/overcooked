<#
.SYNOPSIS
  關閉 T17 展示備援（compose.standby.yaml）：停止容器、快速通道與主機上的 YOLO；資料 volume 保留。
#>
$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Compose = @('compose', '-p', 'overcooked-standby', '--env-file', '.env.standby', '-f', 'compose.yaml', '-f', 'compose.standby.yaml',
             '--profile', 'tunnel')
docker @Compose stop
$YoloPidFile = Join-Path $Root '.dev\standby-yolo.pid'
if (Test-Path $YoloPidFile) {
    Stop-Process -Id (Get-Content $YoloPidFile) -ErrorAction SilentlyContinue
    Remove-Item $YoloPidFile
}
Write-Host '展示環境已關閉（資料保留；下次 standby-up 會取得新的網址）。'
