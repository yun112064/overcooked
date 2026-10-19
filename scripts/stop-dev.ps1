<#
.SYNOPSIS
  關閉 start-dev.ps1 開啟的所有服務視窗，並停止資料庫容器（資料保留）。

.PARAMETER KeepDb
  只關服務視窗，資料庫容器繼續執行。
#>
param([switch]$KeepDb)

$Root = Split-Path -Parent $PSScriptRoot
$PidFile = Join-Path $Root '.dev\pids.txt'

if (Test-Path $PidFile) {
    foreach ($line in Get-Content $PidFile) {
        $name, $procId = $line -split '=', 2
        if (Get-Process -Id $procId -ErrorAction SilentlyContinue) {
            # /T 連同視窗裡的 python／node 子程序一起結束
            taskkill /T /F /PID $procId *> $null
            Write-Host "已關閉 $name"
        }
    }
    Remove-Item $PidFile
} else {
    Write-Host '沒有由 start-dev 開啟的服務。'
}

if (-not $KeepDb) {
    $Db = 'overcooked-dev-db'
    $EnvFile = Join-Path $Root '.env.dev'
    if (Test-Path $EnvFile) {
        $line = Get-Content $EnvFile -Encoding UTF8 | Where-Object { $_ -match '^\s*DEV_DB_CONTAINER\s*=' } | Select-Object -First 1
        if ($line -and $line.Split('=', 2)[1].Trim()) { $Db = $line.Split('=', 2)[1].Trim() }
    }
    docker stop $Db *> $null
    if ($LASTEXITCODE -eq 0) { Write-Host "已停止資料庫容器 $Db（資料保留）" }
}
