<#
.SYNOPSIS
  T17 正式環境（單機）平日啟動：知識庫／小幫手庫／重排容器 → YOLO、T11 知識、飲食小幫手原生程序 → 主系統與 named tunnel。
.DESCRIPTION
  設定讀 .env.prod（範本 .env.prod.example）；先跑 python -m ops.preflight 檢查。
  第一次或更新程式時先執行 python -m ops.deploy --env-file .env.prod（部署前備份、建置、Alembic 升級）。
  三個原生服務只綁 127.0.0.1，容器經 host.docker.internal 連線；對外只有 cloudflared 轉到 frontend。
  飲食小幫手以雲端模型生成回答：本機只做檢索與重排，回答經 backend（127.0.0.1:PROD_BACKEND_PORT）以 T10 額度呼叫 OpenAI。
  Ollama（bge-m3、embeddinggemma 嵌入）需已在本機執行。日誌與 PID 在 .dev\prod-*.log／.pid。
.PARAMETER Stop
  停止三個原生服務與主系統容器（資料保留）。
#>
param([switch]$Stop)
$ErrorActionPreference = 'Continue'  # 外部指令一律檢查 $LASTEXITCODE
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$StateDir = Join-Path $Root '.dev'
New-Item -ItemType Directory -Force $StateDir | Out-Null
$EnvFile = Join-Path $Root '.env.prod'
$Compose = @('compose', '--env-file', '.env.prod', '-f', 'compose.yaml', '-f', 'compose.prod.yaml')

function Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Warn($msg) { Write-Host "    $msg" -ForegroundColor Yellow }
function Fail($msg) { Write-Host "錯誤：$msg" -ForegroundColor Red; exit 1 }
function Read-EnvFile($path) {
    $values = [ordered]@{}
    foreach ($line in Get-Content $path -Encoding UTF8) {
        $t = $line.Trim()
        if ($t -and -not $t.StartsWith('#') -and $t.Contains('=')) {
            $k, $v = $t.Split('=', 2)
            $values[$k.Trim()] = $v.Trim().Trim('"').Trim("'")
        }
    }
    $values
}
$Services = @(
    @{ Name = 'yolo'; Port = 8765 },
    @{ Name = 'knowledge'; Port = 8091 },
    @{ Name = 'assistant'; Port = 8094 }
)

if ($Stop) {
    foreach ($s in $Services) {
        $pidFile = Join-Path $StateDir "prod-$($s.Name).pid"
        if (Test-Path $pidFile) { Stop-Process -Id (Get-Content $pidFile) -ErrorAction SilentlyContinue; Remove-Item $pidFile }
    }
    docker @Compose stop
    Write-Host '正式環境已停止（資料保留；知識庫與小幫手庫容器保持執行）。'
    exit 0
}

if (-not (Test-Path $EnvFile)) { Fail '找不到 .env.prod（複製 .env.prod.example 後填入）。' }
docker info *> $null
if ($LASTEXITCODE -ne 0) { Fail '連不到 Docker，請先開啟 Docker Desktop。' }
$Py = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Py)) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $cmd) { Fail '找不到 Python（先執行一次 scripts\start-dev.ps1 建立 .venv）。' }
    $Py = $cmd.Source
}
& $Py -m ops.preflight --env-file .env.prod
if ($LASTEXITCODE -ne 0) { Fail '設定檢查未通過。' }
$Cfg = Read-EnvFile $EnvFile
function Val($key, $default = '') { if ($Cfg.Contains($key) -and $Cfg[$key]) { $Cfg[$key] } else { $default } }

Step '啟動知識庫、飲食小幫手庫與重排容器'
docker compose --env-file .env.prod -f deploy/team/compose.yaml up -d --wait
if ($LASTEXITCODE -ne 0) { Fail '附屬容器啟動失敗。' }

# 子程序共用的環境（只在本程序內；秘密不寫在指令列）
$env:KNOWLEDGE_DATABASE_URL = "postgresql://postgres:$(Val 'KNOWLEDGE_DB_PASSWORD')@127.0.0.1:55437/meal_knowledge"
$env:KNOWLEDGE_SERVICE_TOKEN = Val 'KNOWLEDGE_SERVICE_TOKEN'
$env:ASSISTANT_DATABASE_URL = "postgresql://postgres:$(Val 'ASSISTANT_DB_PASSWORD')@127.0.0.1:55436/overcooked_assistant"
$env:ASSISTANT_SERVICE_TOKEN = Val 'ASSISTANT_SERVICE_TOKEN'
$env:RAG_LLM_PROVIDER = 'openai'
# 回答由同一台電腦的 backend 以雲端模型生成（只綁 127.0.0.1，不經 tunnel、不需 Cloudflare Access）
$env:CLOUD_INTERNAL_URL = "http://127.0.0.1:$(Val 'PROD_BACKEND_PORT' '18000')"
$env:CLOUD_INTERNAL_TOKEN = Val 'INTERNAL_API_TOKEN'
if (Val 'ASSISTANT_DATA_DIR') { $env:ASSISTANT_DATA_DIR = Val 'ASSISTANT_DATA_DIR' }
$env:INFERENCE_TOKEN = Val 'RECOGNITION_SERVICE_TOKEN'
$env:INFERENCE_HOST = '127.0.0.1'
$env:INFERENCE_PORT = '8765'

Step '核對 T11 審核知識並檢查索引（本機 embeddinggemma，不呼叫付費 API）'
& $Py -m knowledge_rag.bootstrap
if ($LASTEXITCODE -ne 0) { Warn '知識庫準備失敗；知識服務仍會啟動，但查詢會回報不可用。' }

function Start-Native($name, $port, $exe, [string[]]$arguments) {
    $pidFile = Join-Path $StateDir "prod-$name.pid"
    if ((Test-Path $pidFile) -and (Get-Process -Id (Get-Content $pidFile) -ErrorAction SilentlyContinue)) {
        Write-Host "    $name 已在執行，略過"; return
    }
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
        Warn "port $port 已被其他程式使用（可能是開發環境），略過 $name。"; return
    }
    Step "啟動 $name（127.0.0.1:$port，日誌 .dev\prod-$name.log）"
    $p = Start-Process -FilePath $exe -ArgumentList $arguments -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $StateDir "prod-$name.log") -RedirectStandardError (Join-Path $StateDir "prod-$name.err.log")
    Set-Content $pidFile $p.Id
}

$YoloPy = Join-Path $Root '.venv-yolo\Scripts\python.exe'
if (Test-Path $YoloPy) { Start-Native 'yolo' 8765 $YoloPy @('inference/server.py') }
else { Warn '.venv-yolo 不存在（先執行一次 scripts\start-dev.ps1）；辨識會顯示不可用。' }
Start-Native 'knowledge' 8091 $Py @('-m', 'uvicorn', 'knowledge_rag.server:create_app', '--factory', '--host', '127.0.0.1', '--port', '8091')
Start-Native 'assistant' 8094 $Py @('-m', 'assistant_rag.server', '--host', '127.0.0.1', '--port', '8094')

foreach ($k in 'KNOWLEDGE_DATABASE_URL', 'KNOWLEDGE_SERVICE_TOKEN', 'ASSISTANT_DATABASE_URL', 'ASSISTANT_SERVICE_TOKEN',
               'CLOUD_INTERNAL_TOKEN', 'INFERENCE_TOKEN') {
    Remove-Item "env:$k" -ErrorAction SilentlyContinue
}

Step '啟動主系統與 named tunnel（不建置、不升級 schema）'
docker @Compose up -d --wait --no-build
if ($LASTEXITCODE -ne 0) {
    Fail '主系統啟動失敗。第一次使用或剛更新程式請先執行 python -m ops.deploy --env-file .env.prod（docker compose -p meal-prod logs backend 查看原因）。'
}
Write-Host ''
Write-Host "正式環境已啟動：https://$(Val 'PUBLIC_HOSTNAME')" -ForegroundColor Green
Warn '整套系統在這台電腦上：關機、睡眠或斷網時整站離線。'
Write-Host '停止：.\scripts\prod-up.ps1 -Stop'
