<#
.SYNOPSIS
  T17 團隊電腦：啟動本機資料庫與重排容器，再以主機原生程序啟動 YOLO、T11 知識服務與飲食小幫手檢索服務。
.DESCRIPTION
  設定讀 deploy/team/.env.team（範本 deploy/team/.env.team.example）；先跑 python -m ops.preflight team 檢查。
  三個服務只綁 127.0.0.1，對外只經 cloudflared Windows 服務的 named tunnel（見 deploy/team/compose.yaml 註解）。
  飲食小幫手以雲端模型生成回答：本機只做檢索與重排，回答經 CLOUD_INTERNAL_URL 由雲端以 T10 額度呼叫 OpenAI。
  Ollama（bge-m3、embeddinggemma 嵌入）需已在本機執行。日誌與 PID 在 .dev\team-*.log／.pid。
.PARAMETER Stop
  停止三個原生服務（容器與資料保留）。
#>
param([switch]$Stop)
$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$StateDir = Join-Path $Root '.dev'
New-Item -ItemType Directory -Force $StateDir | Out-Null
$EnvFile = Join-Path $Root 'deploy\team\.env.team'

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
        $pidFile = Join-Path $StateDir "team-$($s.Name).pid"
        if (Test-Path $pidFile) { Stop-Process -Id (Get-Content $pidFile) -ErrorAction SilentlyContinue; Remove-Item $pidFile }
    }
    Write-Host '團隊電腦服務已停止（容器與資料保留）。'
    exit 0
}

if (-not (Test-Path $EnvFile)) { Fail '找不到 deploy\team\.env.team（複製 deploy\team\.env.team.example 後填入）。' }
$Py = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Py)) { Fail '找不到 .venv（先執行一次 scripts\start-dev.ps1）。' }
& $Py -m ops.preflight team --env-file deploy/team/.env.team
if ($LASTEXITCODE -ne 0) { Fail '設定檢查未通過。' }
$Cfg = Read-EnvFile $EnvFile

Step '啟動知識庫、飲食小幫手庫與重排容器'
docker compose --env-file deploy/team/.env.team -f deploy/team/compose.yaml up -d --wait
if ($LASTEXITCODE -ne 0) { Fail '容器啟動失敗。' }

# 子程序共用的環境（只在本程序內；秘密不寫在指令列）
$env:KNOWLEDGE_DATABASE_URL = "postgresql://postgres:$($Cfg['KNOWLEDGE_DB_PASSWORD'])@127.0.0.1:55437/meal_knowledge"
$env:KNOWLEDGE_SERVICE_TOKEN = $Cfg['KNOWLEDGE_SERVICE_TOKEN']
$env:ASSISTANT_DATABASE_URL = "postgresql://postgres:$($Cfg['ASSISTANT_DB_PASSWORD'])@127.0.0.1:55436/overcooked_assistant"
$env:ASSISTANT_SERVICE_TOKEN = $Cfg['ASSISTANT_SERVICE_TOKEN']
$env:RAG_LLM_PROVIDER = 'openai'
foreach ($k in 'CLOUD_INTERNAL_URL', 'CLOUD_INTERNAL_TOKEN', 'CLOUD_ACCESS_CLIENT_ID', 'CLOUD_ACCESS_CLIENT_SECRET', 'ASSISTANT_DATA_DIR') {
    if ($Cfg.Contains($k)) { Set-Item "env:$k" $Cfg[$k] }
}
$env:INFERENCE_TOKEN = $Cfg['INFERENCE_TOKEN']
$env:INFERENCE_HOST = '127.0.0.1'
$env:INFERENCE_PORT = '8765'

Step '核對 T11 審核知識並檢查索引（本機 embeddinggemma，不呼叫付費 API）'
& $Py -m knowledge_rag.bootstrap
if ($LASTEXITCODE -ne 0) { Warn '知識庫準備失敗；知識服務仍會啟動，但查詢會回報不可用。' }

function Start-Native($name, $port, $exe, [string[]]$arguments) {
    $pidFile = Join-Path $StateDir "team-$name.pid"
    if ((Test-Path $pidFile) -and (Get-Process -Id (Get-Content $pidFile) -ErrorAction SilentlyContinue)) {
        Write-Host "    $name 已在執行，略過"; return
    }
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
        Warn "port $port 已被其他程式使用，略過 $name。"; return
    }
    Step "啟動 $name（127.0.0.1:$port，日誌 .dev\team-$name.log）"
    $p = Start-Process -FilePath $exe -ArgumentList $arguments -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $StateDir "team-$name.log") -RedirectStandardError (Join-Path $StateDir "team-$name.err.log")
    Set-Content $pidFile $p.Id
}

$YoloPy = Join-Path $Root '.venv-yolo\Scripts\python.exe'
if (Test-Path $YoloPy) { Start-Native 'yolo' 8765 $YoloPy @('inference/server.py') }
else { Warn '.venv-yolo 不存在（先執行一次 scripts\start-dev.ps1）；辨識會顯示不可用。' }
Start-Native 'knowledge' 8091 $Py @('-m', 'uvicorn', 'knowledge_rag.server:create_app', '--factory', '--host', '127.0.0.1', '--port', '8091')
Start-Native 'assistant' 8094 $Py @('-m', 'assistant_rag.server', '--host', '127.0.0.1', '--port', '8094')

foreach ($k in 'KNOWLEDGE_DATABASE_URL', 'KNOWLEDGE_SERVICE_TOKEN', 'ASSISTANT_DATABASE_URL', 'ASSISTANT_SERVICE_TOKEN',
               'CLOUD_INTERNAL_TOKEN', 'CLOUD_ACCESS_CLIENT_SECRET', 'INFERENCE_TOKEN') {
    Remove-Item "env:$k" -ErrorAction SilentlyContinue
}
Write-Host ''
Write-Host '團隊電腦服務已啟動；對外由 cloudflared Windows 服務的 named tunnel 提供（Get-Service cloudflared）。' -ForegroundColor Green
Write-Host '停止：.\scripts\team-up.ps1 -Stop'
