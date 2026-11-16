<#
.SYNOPSIS
  關閉 start-dev.ps1 啟動的所有服務（後端、worker、排程、查價、知識 RAG、前端、YOLO、飲食小幫手 assistant／assistant-openai、
  模擬超市 sim-supermarket），並停止資料庫容器與 start-dev 啟動的小幫手容器（資料保留）。

.PARAMETER KeepDb
  只關服務，資料庫容器（含小幫手的資料庫與重排容器）繼續執行。
#>
param([switch]$KeepDb)

$Root = Split-Path -Parent $PSScriptRoot
$PidFile = Join-Path $Root '.dev\pids.txt'

if (Test-Path $PidFile) {
    foreach ($line in Get-Content $PidFile) {
        $name, $procId = $line -split '=', 2
        if (Get-Process -Id $procId -ErrorAction SilentlyContinue) {
            # /T 連同底下的 python／node 子程序一起結束
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
    # 飲食小幫手：只停 start-dev 記錄為由它啟動的容器（pgvector 資料庫與重排服務）
    $AssistantContainersFile = Join-Path $Root '.dev\assistant-containers.txt'
    if (Test-Path $AssistantContainersFile) {
        foreach ($c in (Get-Content $AssistantContainersFile | Where-Object { $_.Trim() } | Sort-Object -Unique)) {
            docker stop $c *> $null
            if ($LASTEXITCODE -eq 0) { Write-Host "已停止小幫手容器 $c（資料保留）" }
        }
        Remove-Item $AssistantContainersFile
    }
}
