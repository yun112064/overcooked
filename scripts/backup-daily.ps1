<#
.SYNOPSIS
  T17 每日備份（單機）：業務庫、T11 知識庫、飲食小幫手庫依序備份，各自複製到 BACKUP_MIRROR_ROOT 並套用保留期。
.DESCRIPTION
  設定讀 .env.prod。任一份失敗即以非零結束碼結束（工作排程器會記錄為失敗），其餘照常執行。
  輸出附加到 .dev\backup-daily.log（只含路徑與結果，不含秘密）。Docker Desktop 必須在執行中。
  註冊排程：.\scripts\install-prod-tasks.ps1
#>
$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$StateDir = Join-Path $Root '.dev'
New-Item -ItemType Directory -Force $StateDir | Out-Null
$Log = Join-Path $StateDir 'backup-daily.log'
$Py = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Py)) { $Py = (Get-Command python -ErrorAction SilentlyContinue).Source }
$env:PYTHONUTF8 = '1'

function Log($msg) { $line = "$(Get-Date -Format s) $msg"; Write-Host $line; Add-Content $Log $line -Encoding UTF8 }

if (-not (Test-Path (Join-Path $Root '.env.prod'))) { Log 'FAILED: 找不到 .env.prod'; exit 1 }
docker info *> $null
if ($LASTEXITCODE -ne 0) { Log 'FAILED: 連不到 Docker'; exit 1 }

$failed = @()
foreach ($side in 'cloud', 'knowledge', 'assistant') {
    $out = & $Py -m ops.backup $side --env-file .env.prod 2>&1 | Out-String
    $code = $LASTEXITCODE
    foreach ($l in ($out.Trim() -split "`r?`n")) { if ($l) { Log "[$side] $l" } }
    if ($code -ne 0) { $failed += $side }
}
if ($failed) { Log "每日備份失敗：$($failed -join ', ')"; exit 1 }
Log '每日備份完成'
