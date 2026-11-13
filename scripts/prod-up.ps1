<#
.SYNOPSIS
  T17 正式環境：在這台電腦以 Docker 啟動整套系統，經 Cloudflare 快速通道（trycloudflare.com 臨時網址）對外。
  部署方式與 t11-t20-demo 相同，另含 T11 營養知識與飲食小幫手（本機模型）。
.DESCRIPTION
  每次執行都可重複（已完成的步驟會略過）：
    - 沒有 .env.prod → 產生（隨機 DB 密碼與內部權杖；OPENAI_API_KEY 由 .env.dev 帶入；不顯示任何秘密）
    - 確認 Ollama 與模型（embeddinggemma、qwen3.5:9b）→ 建置映像 → 啟動三個資料庫
    - 第一次以 install_real_nutrition 安裝經雜湊核對的真實食譜／營養資料包
    - python -m ops.deploy：部署前備份（含獨立位置副本）→ alembic upgrade head
    - T11 知識：knowledge_rag.bootstrap（本機 embeddinggemma，不呼叫付費 API）
    - YOLO 以主機原生 .venv-yolo 執行（GPU），只綁 127.0.0.1；容器經 host.docker.internal 連線
    - 啟動其餘服務與 cloudflared，從日誌取得 https://*.trycloudflare.com，寫入 PUBLIC_HOSTNAME 後重建 backend
  飲食小幫手的語料與索引另以 scripts\assistant-data.ps1 建立（第一次約 1–2 小時）；尚未建立時小幫手顯示暫時無法使用。
  主機不開任何連入埠。網址每次重開都會變；電腦關機或執行 scripts\prod-down.ps1 即離線。
.PARAMETER SkipYolo
  不啟動 YOLO（辨識顯示不可用，仍可手動輸入食材）。
.PARAMETER NoTunnel
  只在本機啟動服務、不開對外網址（驗證用）。
#>
param([switch]$SkipYolo, [switch]$NoTunnel)
$ErrorActionPreference = 'Continue'  # 外部指令一律檢查 $LASTEXITCODE
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$StateDir = Join-Path $Root '.dev'
New-Item -ItemType Directory -Force $StateDir | Out-Null
$env:PYTHONUTF8 = '1'

function Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Info($msg) { Write-Host "    $msg" }
function Warn($msg) { Write-Host "    $msg" -ForegroundColor Yellow }
function Fail($msg) { Write-Host "錯誤：$msg" -ForegroundColor Red; exit 1 }
function Read-EnvFile($path) {
    $values = [ordered]@{}
    if (Test-Path $path) {
        foreach ($line in Get-Content $path -Encoding UTF8) {
            $t = $line.Trim()
            if ($t -and -not $t.StartsWith('#') -and $t.Contains('=')) {
                $k, $v = $t.Split('=', 2)
                $values[$k.Trim()] = $v.Trim().Trim('"').Trim("'")
            }
        }
    }
    $values
}
function Val($map, $key, $default = '') { if ($map.Contains($key) -and $map[$key]) { $map[$key] } else { $default } }

$Compose = @('compose', '--env-file', '.env.prod', '-f', 'compose.yaml', '-f', 'compose.prod.yaml')
function Dc { docker @Compose @args }

# ---------- 1. 前置檢查與設定檔 ----------
docker info *> $null
if ($LASTEXITCODE -ne 0) { Fail '連不到 Docker，請先開啟 Docker Desktop 再執行。' }
$Py = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Py)) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $cmd) { Fail '找不到 Python（先執行一次 scripts\start-dev.ps1 建立 .venv，或安裝 Python 3.12）。' }
    $Py = $cmd.Source
}
Step '準備 .env.prod'
& $Py -m ops.prodenv init-env --env-file .env.prod --dev-env .env.dev
if ($LASTEXITCODE -ne 0) { Fail '無法建立 .env.prod。' }
& $Py -m ops.preflight --env-file .env.prod
if ($LASTEXITCODE -ne 0) { Fail '設定檢查未通過（修改 .env.prod 後重跑）。' }
$Prod = Read-EnvFile (Join-Path $Root '.env.prod')
$Dev = Read-EnvFile (Join-Path $Root '.env.dev')
foreach ($key in 'PROJECT_DATA_ROOT', 'PROJECT_RUNTIME_ROOT', 'PROJECT_BACKUP_ROOT', 'ASSISTANT_DATA_DIR') {
    New-Item -ItemType Directory -Force (Val $Prod $key) | Out-Null
}

# ---------- 2. Ollama（嵌入與小幫手回答，主機 GPU） ----------
Step '確認 Ollama 與模型'
$OllamaExe = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'
function Get-OllamaModels { try { (Invoke-RestMethod http://127.0.0.1:11434/api/tags -TimeoutSec 5).models.name } catch { $null } }
$models = Get-OllamaModels
if (-not $models -and (Test-Path $OllamaExe)) {
    Start-Process -FilePath $OllamaExe -ArgumentList 'serve' -WindowStyle Hidden
    for ($i = 0; $i -lt 15 -and -not $models; $i++) { Start-Sleep 2; $models = Get-OllamaModels }
}
if (-not $models) {
    Warn 'Ollama 沒有在執行：T11 知識與飲食小幫手會顯示不可用（開啟 Ollama 後重跑）。'
} else {
    $llm = Val $Prod 'ASSISTANT_LLM_MODEL' 'qwen3.5:9b-q4_K_M'
    $missing = @('embeddinggemma:latest', $llm) | Where-Object { $_ -notin $models }
    if ($missing) { Warn "Ollama 缺少模型：$($missing -join '、')（ollama pull <名稱>）；相關功能會顯示不可用。" }
    else { Info "embeddinggemma、$llm 已就緒" }
}
if (-not (Test-Path (Join-Path (Val $Prod 'ASSISTANT_RERANKER_MODEL_DIR') 'model.safetensors'))) {
    Warn '找不到重排模型權重（ASSISTANT_RERANKER_MODEL_DIR）；飲食小幫手會顯示不可用。'
}

# ---------- 3. 映像與資料庫 ----------
Step '建置映像（第一次約數分鐘）'
Dc build backend frontend knowledge-rag
if ($LASTEXITCODE -ne 0) { Fail '映像建置失敗。' }
Step '啟動資料庫（meal-prod-db、meal-prod-knowledge-db、meal-prod-assistant-db）'
Dc up -d --wait db knowledge-db assistant-db
if ($LASTEXITCODE -ne 0) { Fail '資料庫容器啟動失敗。' }

if (-not (Val $Prod 'DATABASE_URL')) {
    $DbEnv = Join-Path $StateDir 'prod-db.env'
    if (-not (Test-Path $DbEnv)) {
        $ProdDb = Val $Prod 'PROD_DATABASE' 'meal_planner_prod'
        Step "第一次：安裝真實食譜／營養資料包到新資料庫 $ProdDb（核對雜湊，約一分鐘）"
        # 管理連線只從環境變數帶入容器，不出現在指令列
        $env:INSTALL_DATABASE_ADMIN_URL = "postgresql+psycopg://postgres:$($Prod['POSTGRES_PASSWORD'])@db:5432/postgres"
        Dc run --rm --no-deps -w /app -e INSTALL_DATABASE_ADMIN_URL -v "${StateDir}:/app/.test-runtime" backend `
            python scripts/install_real_nutrition.py --database $ProdDb --output-env /app/.test-runtime/prod-db.env
        $code = $LASTEXITCODE
        Remove-Item env:INSTALL_DATABASE_ADMIN_URL -ErrorAction SilentlyContinue
        if ($code -ne 0) { Fail '資料包安裝失敗（新資料庫保留供診斷；改 .env.prod 的 PROD_DATABASE 換新名稱再試）。' }
    }
    & $Py -m ops.prodenv merge --env-file .env.prod --from .dev/prod-db.env
    if ($LASTEXITCODE -ne 0) { Fail '無法帶入資料庫設定。' }
    $Prod = Read-EnvFile (Join-Path $Root '.env.prod')
}

Step '部署前備份與 schema 升級（python -m ops.deploy）'
& $Py -m ops.deploy --env-file .env.prod
if ($LASTEXITCODE -ne 0) { Fail '部署前備份或 alembic 升級失敗，服務未更新。' }

# ---------- 4. T11 知識（本機 embeddinggemma） ----------
Step '核對 T11 審核知識並檢查索引（不呼叫付費 API）'
Dc run --rm rag-tools python -m knowledge_rag.bootstrap
if ($LASTEXITCODE -ne 0) { Warn '知識庫準備失敗；知識說明會顯示不可用，其他功能不受影響。' }

# ---------- 5. YOLO（主機原生，GPU） ----------
$YoloPidFile = Join-Path $StateDir 'prod-yolo.pid'
$YoloPort = Val $Prod 'PROD_YOLO_PORT' '8776'
$yoloRunning = (Test-Path $YoloPidFile) -and (Get-Process -Id (Get-Content $YoloPidFile) -ErrorAction SilentlyContinue)
if ($SkipYolo) {
    Warn 'YOLO 未啟動（-SkipYolo）；辨識會顯示不可用，仍可手動輸入食材。'
} elseif ($yoloRunning) {
    Info 'YOLO 已在執行，略過'
} else {
    $YoloPy = Val $Dev 'YOLO_PYTHON' (Join-Path $Root '.venv-yolo\Scripts\python.exe')
    $Weights = Val $Dev 'INFERENCE_WEIGHTS' 'inference/models/yolo26s-food41-denoise-veg3-ft-20261108.pt'
    if (-not [IO.Path]::IsPathRooted($Weights)) { $Weights = [IO.Path]::GetFullPath((Join-Path $Root $Weights)) }
    if (-not (Test-Path $YoloPy) -or -not (Test-Path $Weights)) {
        Warn 'YOLO 環境不存在（.venv-yolo 或權重檔；先執行一次 scripts\start-dev.ps1 建立）；辨識會顯示不可用。'
    } elseif (Get-NetTCPConnection -LocalPort $YoloPort -State Listen -ErrorAction SilentlyContinue) {
        Warn "port $YoloPort 已被其他程式使用，略過 YOLO（可在 .env.prod 改 PROD_YOLO_PORT）。"
    } else {
        Step "啟動 YOLO（127.0.0.1:$YoloPort，日誌 .dev\prod-yolo.log）"
        $env:INFERENCE_TOKEN = $Prod['INFERENCE_TOKEN']
        $env:INFERENCE_WEIGHTS = $Weights
        $env:INFERENCE_HOST = '127.0.0.1'
        $env:INFERENCE_PORT = $YoloPort
        $env:INFERENCE_DEVICE = Val $Prod 'INFERENCE_DEVICE' (Val $Dev 'INFERENCE_DEVICE' 'cpu')
        $p = Start-Process -FilePath $YoloPy -ArgumentList 'inference/server.py' -WorkingDirectory $Root -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $StateDir 'prod-yolo.log') -RedirectStandardError (Join-Path $StateDir 'prod-yolo.err.log')
        Set-Content $YoloPidFile $p.Id
        Remove-Item env:INFERENCE_TOKEN -ErrorAction SilentlyContinue
    }
}

# ---------- 6. 服務與快速通道 ----------
Step '啟動 backend、worker、scheduler、知識 RAG、飲食小幫手、重排、frontend'
Dc up -d --wait backend worker scheduler knowledge-rag assistant-reranker assistant-rag frontend
if ($LASTEXITCODE -ne 0) { Fail '服務啟動失敗（docker compose -p meal-prod logs <服務> 查看原因）。' }

$active = "$(Dc exec -T assistant-db psql -U postgres -d overcooked_assistant -tAc "SELECT count(*) FROM index_versions WHERE status = 'ready'")".Trim()
if ($active -eq '0' -or -not $active) {
    Warn '飲食小幫手還沒有索引：執行 .\scripts\assistant-data.ps1 爬取語料並建索引（建好後不必重啟，下一次提問即生效）。'
}

$Url = $null
if ($NoTunnel) {
    Warn '未開對外網址（-NoTunnel）；本機沒有發佈任何連入埠。'
} else {
    Step '開啟 Cloudflare 快速通道（每次網址都不同）'
    Dc --profile tunnel up -d --force-recreate --no-deps cloudflared
    if ($LASTEXITCODE -ne 0) { Fail 'cloudflared 啟動失敗。' }
    for ($i = 0; $i -lt 30 -and -not $Url; $i++) {
        Start-Sleep -Seconds 2
        $log = cmd /c 'docker logs meal-prod-cloudflared 2>&1' | Out-String
        $Url = "$($log | & $Py -m ops.prodenv tunnel-url)".Trim()
    }
    if (-not $Url) { Fail '60 秒內沒有從 cloudflared 日誌取得網址：docker logs meal-prod-cloudflared' }
    & $Py -m ops.prodenv set --env-file .env.prod PUBLIC_HOSTNAME ($Url -replace '^https://', '')
    Step '以新網址重建 backend（ALLOWED_ORIGINS）'
    Dc up -d --wait --no-deps backend
    if ($LASTEXITCODE -ne 0) { Fail 'backend 重建失敗。' }
}

$health = Dc exec -T frontend wget -qO- http://localhost/api/health
if ($LASTEXITCODE -ne 0) { Warn "經 frontend 呼叫 /api/health 失敗：$health" }
Write-Host ''
if ($Url) {
    Write-Host "正式環境已上線：$Url" -ForegroundColor Green
    Warn '快速通道網址每次重開都會變；電腦關機、睡眠或斷網即離線。'
} else {
    Write-Host '正式環境已在本機啟動（未開對外網址）。' -ForegroundColor Green
}
Info '關閉：.\scripts\prod-down.ps1（資料保留）'
