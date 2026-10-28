<#
.SYNOPSIS
  單機 demo：在這台電腦以 Docker 啟動整套系統，經 Cloudflare 快速通道（trycloudflare.com 臨時網址）對外。

.DESCRIPTION
  每次執行都可重複（已完成的步驟會略過）：
    - 沒有 .env.demo → 產生（隨機 DB 密碼與內部權杖；OPENAI_API_KEY、KNOWLEDGE_AUTHORIZATION_REF 由 .env.dev 帶入；不顯示任何秘密）
    - 建置映像 → 啟動 db、knowledge-db → alembic upgrade head
    - 資料庫沒有食譜 → 從工作簿匯入；推估表是空的 → 匯入 data/seed 資料種子（冪等）
    - YOLO 以主機原生 .venv-yolo 執行（GPU），只綁 127.0.0.1；容器經 host.docker.internal 連線
    - 啟動其餘服務與 cloudflared，從日誌取得 https://*.trycloudflare.com，寫入 PUBLIC_HOSTNAME 後重建 backend
    - 尚無啟用中的知識索引 → 建索引（呼叫 OpenAI 向量化約 200 筆，約 NT$0.1）
  主機不開任何連入埠；只有 frontend 經 cloudflared 對外，/api/internal/* 對外一律 404。
  網址每次重開都會變；電腦關機或執行 scripts/demo-down.ps1 即離線。

.PARAMETER SkipYolo
  不啟動 YOLO（辨識顯示不可用，仍可手動輸入食材）。
.PARAMETER SkipKnowledgeIndex
  不建立知識索引（不呼叫付費 API；知識說明顯示不可用）。
.PARAMETER NoTunnel
  只在本機啟動服務、不開對外網址（驗證用）。
#>
param([switch]$SkipYolo, [switch]$SkipKnowledgeIndex, [switch]$NoTunnel)

$ErrorActionPreference = 'Continue'  # 外部指令一律檢查 $LASTEXITCODE
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$StateDir = Join-Path $Root '.dev'
New-Item -ItemType Directory -Force $StateDir | Out-Null
. (Join-Path $PSScriptRoot 'dev-env.ps1')  # Read-DevConfig、Get-DevValue

function Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Info($msg) { Write-Host "    $msg" }
function Warn($msg) { Write-Host "    $msg" -ForegroundColor Yellow }
function Fail($msg) { Write-Host "錯誤：$msg" -ForegroundColor Red; exit 1 }

$Compose = @('compose', '-p', 'overcooked-demo', '--env-file', '.env.demo', '-f', 'compose.yaml', '-f', 'compose.demo.yaml')
function Dc { docker @Compose @args }
function Sql($db, $query) { "$(Dc exec -T db psql -U postgres -d $db -tAc $query)".Trim() }

# ---------- 1. 前置檢查與設定檔 ----------
docker info *> $null
if ($LASTEXITCODE -ne 0) { Fail '連不到 Docker，請先開啟 Docker Desktop 再執行。' }
$Py = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Py)) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $cmd) { Fail '找不到 Python（先執行一次 start-dev.ps1 建立 .venv，或安裝 Python 3.12）。' }
    $Py = $cmd.Source
}
Step '準備 .env.demo'
& $Py -m ops.demo init-env --env-file .env.demo --dev-env .env.dev
if ($LASTEXITCODE -ne 0) { Fail '無法建立 .env.demo。' }
$Demo = Read-DevConfig (Join-Path $Root '.env.demo')
$DevFile = Join-Path $Root '.env.dev'
$Dev = if (Test-Path $DevFile) { Read-DevConfig $DevFile } else { [ordered]@{} }

# ---------- 2. 映像與資料庫 ----------
Step '建置映像（第一次約數分鐘）'
Dc build backend knowledge-rag frontend
if ($LASTEXITCODE -ne 0) { Fail '映像建置失敗。' }

Step '啟動資料庫（overcooked-demo-db、overcooked-demo-knowledge-db；資料在 demo 專用 volume）'
Dc up -d --wait db knowledge-db
if ($LASTEXITCODE -ne 0) { Fail '資料庫容器啟動失敗。' }

Step '套用資料庫 migration（alembic upgrade head）'
Dc run --rm --no-deps backend alembic -c /app/alembic.ini upgrade head
if ($LASTEXITCODE -ne 0) { Fail 'alembic upgrade 失敗。' }

if ((Sql meal_planner 'SELECT count(*) FROM recipes') -eq '0') {
    Step '第一次：從工作簿匯入食譜（約一分鐘）'
    Dc run --rm tools python scripts/import_recipes.py --excel data/recipe_dataset_1027.xlsx `
        --reviews config/recipe_reviews.json --sources config/recipe_sources.json --report /tmp/import-report.json | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail '食譜匯入失敗。' }
}
if ((Sql meal_planner 'SELECT count(*) FROM conversion_factor_estimates') -eq '0') {
    Step '第一次：匯入資料種子（推估、營養知識、價格快照；免費、冪等）'
    Dc run --rm tools python -m app.jobs.import_seed_data
    if ($LASTEXITCODE -ne 0) { Fail '資料種子匯入失敗。' }
}

# ---------- 3. YOLO（主機原生，GPU） ----------
$YoloPidFile = Join-Path $StateDir 'demo-yolo.pid'
$YoloPort = Get-DevValue $Demo 'DEMO_YOLO_PORT' '8775'
$yoloRunning = (Test-Path $YoloPidFile) -and (Get-Process -Id (Get-Content $YoloPidFile) -ErrorAction SilentlyContinue)
if ($SkipYolo) {
    Warn 'YOLO 未啟動（-SkipYolo）；辨識會顯示不可用，仍可手動輸入食材。'
} elseif ($yoloRunning) {
    Info 'YOLO 已在執行，略過'
} else {
    $YoloPy = Get-DevValue $Dev 'YOLO_PYTHON' (Join-Path $Root '.venv-yolo\Scripts\python.exe')
    $Weights = Get-DevValue $Dev 'INFERENCE_WEIGHTS' 'inference/models/yolo26s-food41-denoise-seed45-50e.pt'
    if (-not [IO.Path]::IsPathRooted($Weights)) { $Weights = [IO.Path]::GetFullPath((Join-Path $Root $Weights)) }
    if (-not (Test-Path $YoloPy) -or -not (Test-Path $Weights)) {
        Warn 'YOLO 環境不存在（.venv-yolo 或權重檔；先執行一次 start-dev.ps1 建立）；辨識會顯示不可用，仍可手動輸入食材。'
    } elseif (Get-NetTCPConnection -LocalPort $YoloPort -State Listen -ErrorAction SilentlyContinue) {
        Warn "port $YoloPort 已被其他程式使用，略過 YOLO（可在 .env.demo 改 DEMO_YOLO_PORT）。"
    } else {
        Step "啟動 YOLO（127.0.0.1:$YoloPort，日誌 .dev\demo-yolo.log）"
        $env:INFERENCE_TOKEN = Get-DevValue $Demo 'INFERENCE_TOKEN'
        $env:INFERENCE_WEIGHTS = $Weights
        $env:INFERENCE_DEVICE = Get-DevValue $Demo 'INFERENCE_DEVICE' 'cpu'
        $env:INFERENCE_HOST = '127.0.0.1'
        $env:INFERENCE_PORT = $YoloPort
        $env:PYTHONUNBUFFERED = '1'
        $p = Start-Process $YoloPy -ArgumentList 'inference/server.py' -WorkingDirectory $Root -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $StateDir 'demo-yolo.log') -RedirectStandardError (Join-Path $StateDir 'demo-yolo.err.log')
        Set-Content $YoloPidFile $p.Id
        Remove-Item env:INFERENCE_TOKEN
    }
}

# ---------- 4. 其餘服務 ----------
Step '啟動 backend、worker、scheduler、知識 RAG、frontend'
Dc up -d --wait backend worker scheduler knowledge-rag frontend
if ($LASTEXITCODE -ne 0) { Fail '服務啟動失敗，請看 docker compose -p overcooked-demo logs。' }

# ---------- 5. 快速通道 ----------
$Url = $null
if ($NoTunnel) {
    Warn '未開對外網址（-NoTunnel）；服務只在 Docker 內部網路。'
} else {
    Step '開啟 Cloudflare 快速通道（每次網址都不同）'
    Dc --profile tunnel up -d --force-recreate --no-deps cloudflared
    if ($LASTEXITCODE -ne 0) { Fail 'cloudflared 啟動失敗。' }
    for ($i = 0; $i -lt 30 -and -not $Url; $i++) {
        Start-Sleep -Seconds 2
        $log = cmd /c 'docker logs overcooked-demo-cloudflared 2>&1' | Out-String
        $Url = "$($log | & $Py -m ops.demo tunnel-url)".Trim()
    }
    if (-not $Url) { Fail '60 秒內沒有從 cloudflared 日誌取得網址：docker logs overcooked-demo-cloudflared' }
    & $Py -m ops.demo set --env-file .env.demo PUBLIC_HOSTNAME ($Url -replace '^https://', '')
    Step '以新網址重建 backend（ALLOWED_ORIGINS）'
    Dc up -d --wait --no-deps backend
    if ($LASTEXITCODE -ne 0) { Fail 'backend 重建失敗。' }
}

# ---------- 6. 知識索引（付費向量化） ----------
$active = "$(Dc exec -T knowledge-db psql -U postgres -d meal_knowledge -tAc "SELECT count(*) FROM knowledge_indexes WHERE status = 'active'")".Trim()
if ($active -ne '0') {
    Info '知識索引已啟用，略過'
} elseif ($SkipKnowledgeIndex) {
    Warn '未建立知識索引（-SkipKnowledgeIndex）；知識說明會顯示不可用。'
} elseif (-not (Get-DevValue $Demo 'OPENAI_API_KEY')) {
    Warn '.env.demo 沒有 OPENAI_API_KEY，無法建立知識索引；知識說明會顯示不可用。'
} else {
    Step '第一次：建立知識索引（OpenAI 向量化約 200 筆，約 NT$0.1）'
    $Version = 'seed-' + (Get-Date -Format 'yyyyMMdd')
    Dc run --rm tools python -m knowledge_rag.admin index build --version $Version
    if ($LASTEXITCODE -ne 0) { Warn '知識索引建立失敗；知識說明會顯示不可用，其他功能不受影響。' }
}

# ---------- 7. 結果 ----------
$health = Dc exec -T frontend wget -qO- http://localhost/api/health
if ($LASTEXITCODE -ne 0) { Warn "經 frontend 呼叫 /api/health 失敗：$health" }
Write-Host ''
if ($Url) {
    Write-Host "demo 已上線：$Url" -ForegroundColor Green
    Write-Host '（快速通道網址每次重開都會變；電腦關機或執行 scripts\demo-down.ps1 即離線）'
} else {
    Write-Host 'demo 服務已在本機啟動（未開對外網址）。' -ForegroundColor Green
}
Write-Host '關閉：.\scripts\demo-down.ps1（資料保留）'
