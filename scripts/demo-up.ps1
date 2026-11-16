<#
.SYNOPSIS
  單機 demo：在這台電腦以 Docker 啟動整套系統，經 Cloudflare 快速通道（trycloudflare.com 臨時網址）對外。

.DESCRIPTION
  每次執行都可重複（已完成的步驟會略過）：
    - 沒有 .env.demo → 產生（隨機 DB 密碼與內部權杖；OPENAI_API_KEY、KNOWLEDGE_AUTHORIZATION_REF 由 .env.dev 帶入；不顯示任何秘密）
    - 舊的 .env.demo → ops.demo ensure-keys 只補上新版缺少的鍵（既有值不改）
    - 建置映像 → 啟動 db、knowledge-db → 資料庫版本落後程式時先做部署前備份（ops.backup，失敗即中止）→ alembic upgrade head
    - 資料庫沒有食譜 → 從工作簿匯入；推估表是空的 → 匯入 data/seed 資料種子（冪等）
    - 每次都載入 release_data/nutrition-v1（食譜封面、T04 營養證據；冪等、單一交易，失敗不留部分資料）
    - YOLO 以主機原生 .venv-yolo 執行（GPU），只綁 127.0.0.1；容器經 host.docker.internal 連線
    - 啟動其餘服務、模擬超市、飲食小幫手（語料與重排權重目錄都存在時）
    - 開兩條快速通道（frontend 與模擬超市），寫入 PUBLIC_HOSTNAME、SIM_MARKET_URL 後只重建一次 backend
    - 尚無啟用中的知識索引 → 建索引（呼叫 OpenAI 向量化約 200 筆，約 NT$0.1）
  主機不開任何連入埠；只有 frontend 與模擬超市經 cloudflared 對外，/api/internal/* 對外一律 404。
  migration 失敗時絕不執行 alembic downgrade：以部署前備份還原到新資料庫（ops.restore）。
  網址每次重開都會變；電腦關機或執行 scripts/demo-down.ps1 即離線。

.PARAMETER SkipYolo
  不啟動 YOLO（辨識顯示不可用，仍可手動輸入食材）。
.PARAMETER SkipKnowledgeIndex
  不建立知識索引（不呼叫付費 API；知識說明顯示不可用）。
.PARAMETER NoTunnel
  只在本機啟動服務、不開對外網址（驗證用）；模擬超市交接回 503。
.PARAMETER SkipMarket
  不啟動模擬超市與其快速通道（SIM_MARKET_URL 清空，交接回 503 MARKET_UNAVAILABLE）。
.PARAMETER SkipAssistant
  不建置、不啟動飲食小幫手（小幫手顯示不可用）。
.PARAMETER BackupRoot
  部署前備份的主機目錄（預設 .env.demo 的 DEMO_BACKUP_ROOT，否則 repo 旁的 overcooked-demo_backups；在 Git 之外）。
#>
param([switch]$SkipYolo, [switch]$SkipKnowledgeIndex, [switch]$NoTunnel, [switch]$SkipMarket, [switch]$SkipAssistant,
      [string]$BackupRoot)

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
# 從 cloudflared 容器日誌取得快速通道網址（最多約 60 秒）；取不到回 $null
function Get-TunnelUrl($container) {
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 2
        $log = cmd /c "docker logs $container 2>&1" | Out-String
        $found = "$($log | & $Py -m ops.demo tunnel-url)".Trim()
        if ($found) { return $found }
    }
    return $null
}

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
& $Py -m ops.demo ensure-keys --env-file .env.demo --dev-env .env.dev
if ($LASTEXITCODE -ne 0) { Fail '無法補齊 .env.demo 的新鍵。' }
# 上次的超市快速通道網址已失效：先清空（交接回 503），開出新通道後才寫入，backend 只會拿到有效網址或空值
& $Py -m ops.demo set --env-file .env.demo SIM_MARKET_URL
if ($LASTEXITCODE -ne 0) { Fail '無法更新 .env.demo。' }
$Demo = Read-DevConfig (Join-Path $Root '.env.demo')
$DevFile = Join-Path $Root '.env.dev'
$Dev = if (Test-Path $DevFile) { Read-DevConfig $DevFile } else { [ordered]@{} }

# 飲食小幫手：語料目錄（Git 之外）與重排模型權重目錄都存在才啟動
$AssistantDataDir = Get-DevValue $Demo 'ASSISTANT_DATA_DIR'
$RerankerDir = Get-DevValue $Demo 'ASSISTANT_RERANKER_MODEL_DIR'
$UseAssistant = (-not $SkipAssistant) -and $AssistantDataDir -and $RerankerDir -and
    (Test-Path $AssistantDataDir -PathType Container) -and (Test-Path $RerankerDir -PathType Container)

# ---------- 2. 映像與資料庫 ----------
Step '建置映像（第一次約數分鐘）'
# 核心映像失敗才中止；模擬超市與飲食小幫手是選用功能，建置失敗只降級成不可用
Dc build backend knowledge-rag frontend
if ($LASTEXITCODE -ne 0) { Fail '映像建置失敗。' }
if (-not $SkipMarket) {
    Dc --profile sim-market build sim-supermarket
    if ($LASTEXITCODE -ne 0) { Warn '模擬超市映像建置失敗；採買交接會回 503 MARKET_UNAVAILABLE。'; $SkipMarket = $true }
}
$AssistantBuildFailed = $false
if ($UseAssistant) {
    Dc --profile assistant build assistant-rag
    if ($LASTEXITCODE -ne 0) { $AssistantBuildFailed = $true; $UseAssistant = $false }
}

Step '啟動資料庫（overcooked-demo-db、overcooked-demo-knowledge-db；資料在 demo 專用 volume）'
Dc up -d --wait db knowledge-db
if ($LASTEXITCODE -ne 0) { Fail '資料庫容器啟動失敗。' }

# 部署前備份：既有資料庫的版本與程式的 head 不同時，先備份再升級（新 migration 的 downgrade 會刪除載入的證據，
# 復原一律從備份還原，不執行 downgrade）。全新的 volume 沒有 alembic_version，不需備份。
Step '確認資料庫 migration 版本'
$Current = ''
$hasDb = "$(Dc exec -T db psql -U postgres -d postgres -tAc "SELECT count(*) FROM pg_database WHERE datname = 'meal_planner'")".Trim()
if ($LASTEXITCODE -ne 0) { Fail '無法讀取 demo 資料庫狀態。' }
if ($hasDb -ne '0') {
    $hasTable = Sql meal_planner "SELECT coalesce(to_regclass('public.alembic_version')::text, '')"
    if ($LASTEXITCODE -ne 0) { Fail '無法讀取 demo 資料庫的 alembic_version。' }
    if ($hasTable) {
        $Current = Sql meal_planner 'SELECT version_num FROM alembic_version'
        if ($LASTEXITCODE -ne 0) { Fail '無法讀取 demo 資料庫目前的 migration 版本。' }
    }
}
$headOut = Dc run --rm --no-deps backend alembic -c /app/alembic.ini heads
if ($LASTEXITCODE -ne 0) { Fail '無法讀取程式的 migration head。' }
$Heads = @($headOut | ForEach-Object { if ("$_" -match '^\s*([0-9A-Za-z_]+) \(head\)') { $Matches[1] } })
if ($Heads.Count -ne 1) { Fail "程式的 migration head 不是唯一一個：$($Heads -join ', ')" }
$Head = $Heads[0]
$BackupPath = $null
if (-not $Current) {
    Info "全新的資料庫（沒有 alembic_version），不需部署前備份；升級到 $Head"
} elseif ($Current -eq $Head) {
    Info "資料庫已在 $Head，不需部署前備份"
} else {
    if (-not $BackupRoot) {
        $BackupRoot = Get-DevValue $Demo 'DEMO_BACKUP_ROOT' (Join-Path (Split-Path -Parent $Root) 'overcooked-demo_backups')
    }
    # 相對路徑以 PowerShell 目前位置（$Root）解析；否則 docker -v 會把它當成具名 volume
    $BackupRoot = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($BackupRoot)
    New-Item -ItemType Directory -Force $BackupRoot | Out-Null
    Step "部署前備份（$Current → $Head；備份目錄 $BackupRoot）"
    # ops.backup 需要主機上的 Data Root：把 demo_data volume 複製到備份目錄旁的暫存處，備份完即刪除
    $Stage = Join-Path $BackupRoot '.data-staging'
    if (Test-Path $Stage) { Remove-Item -Recurse -Force $Stage }
    New-Item -ItemType Directory -Force $Stage | Out-Null
    Dc run --rm --no-deps -v "${Stage}:/stage" backend sh -c 'cp -r /app/data/. /stage/'
    if ($LASTEXITCODE -ne 0) { Fail '無法複製 demo 的 Data Root，未做任何變更（沒有執行 alembic）。' }
    $backupOut = & $Py -m ops.backup cloud --container overcooked-demo-db --database meal_planner --label "pre-$Head" `
        --backup-root $BackupRoot --data-root $Stage
    $backupCode = $LASTEXITCODE
    $backupOut | ForEach-Object { Info "$_" }
    Remove-Item -Recurse -Force $Stage -ErrorAction SilentlyContinue
    $okLine = @($backupOut | Where-Object { "$_" -like 'backup ok: *' }) | Select-Object -First 1
    if ($backupCode -ne 0 -or -not $okLine) { Fail '部署前備份失敗，未做任何變更（沒有執行 alembic）。' }
    $BackupPath = "$okLine".Substring('backup ok: '.Length).Trim()
    & $Py -c 'import sys; from ops.backup import verify_backup; verify_backup(sys.argv[1])' $BackupPath
    if ($LASTEXITCODE -ne 0) { Fail "部署前備份校驗失敗（$BackupPath），未做任何變更（沒有執行 alembic）。" }
    Info "部署前備份：$BackupPath"
}

Step '套用資料庫 migration（alembic upgrade head）'
Dc run --rm --no-deps backend alembic -c /app/alembic.ini upgrade head
if ($LASTEXITCODE -ne 0) {
    Warn '不要執行 alembic downgrade（會刪除已載入的 T04 證據與封面）；舊資料仍在部署前備份。'
    if ($BackupPath) {
        Warn '若資料庫已部分變更，以部署前備份還原到新資料庫後再處理：'
        Warn "  python -m ops.restore `"$BackupPath`" --target-database meal_planner_restored --container overcooked-demo-db --data-target <新的空目錄>"
    }
    Fail 'alembic upgrade 失敗。'
}

if ((Sql meal_planner 'SELECT count(*) FROM recipes') -eq '0') {
    Step '第一次：從工作簿匯入食譜（約一分鐘）'
    Dc run --rm tools python scripts/import_recipes.py --excel data/recipe_dataset_1027.xlsx `
        --reviews config/recipe_reviews.json --sources config/recipe_sources.json --report /tmp/import-report.json | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail '食譜匯入失敗。' }
}
# 每次都執行：載入器冪等且單一交易，失敗不留部分資料，下次自動重試
Step '載入資料包 release_data/nutrition-v1（食譜封面、T04 營養證據）'
Dc run --rm tools python scripts/load_release_bundle.py --bundle release_data/nutrition-v1
if ($LASTEXITCODE -ne 0) { Warn '資料包載入失敗（未留下部分資料，下次 demo-up 會重試）；食譜封面與 T04 營養證據會顯示不可用。' }
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
    $Weights = Get-DevValue $Dev 'INFERENCE_WEIGHTS' 'inference/models/yolo26s-food41-denoise-veg3-ft-20261108.pt'
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
if ($SkipMarket) {
    Warn '模擬超市未啟動（-SkipMarket）；採買交接會回 503 MARKET_UNAVAILABLE。'
} else {
    Step '啟動模擬超市（overcooked-demo-sim-supermarket；不開主機埠）'
    Dc --profile sim-market up -d --no-deps sim-supermarket
    if ($LASTEXITCODE -ne 0) { Warn '模擬超市啟動失敗；採買交接會回 503 MARKET_UNAVAILABLE。'; $SkipMarket = $true }
}
if ($UseAssistant) {
    Step '啟動飲食小幫手（assistant-db、重排、地端與 OpenAI 兩份 assistant-rag）'
    Dc --profile assistant up -d --wait assistant-db assistant-reranker assistant-rag assistant-rag-openai
    if ($LASTEXITCODE -ne 0) {
        Warn '飲食小幫手啟動失敗（docker compose -p overcooked-demo logs assistant-rag）；小幫手會顯示不可用。'
    } else {
        $ready = "$(Dc exec -T assistant-db psql -U postgres -d overcooked_assistant -tAc "SELECT count(*) FROM index_versions WHERE status = 'ready'")".Trim()
        if ($ready -eq '0' -or -not $ready) {
            Warn '飲食小幫手還沒有索引：執行 .\scripts\assistant-data-demo.ps1 爬取語料並建索引（建好後不必重啟，下一次提問即生效）。'
        }
        if (-not (Get-DevValue $Demo 'OPENAI_API_KEY')) { Warn '.env.demo 沒有 OPENAI_API_KEY：OpenAI 版小幫手的回答會失敗（地端版不受影響）。' }
    }
} elseif ($SkipAssistant) {
    Warn '飲食小幫手未啟動（-SkipAssistant）；小幫手會顯示不可用。'
} elseif ($AssistantBuildFailed) {
    Warn '飲食小幫手映像建置失敗（assistant-rag）；小幫手會顯示不可用。'
} else {
    Warn '飲食小幫手未啟動：.env.demo 的 ASSISTANT_DATA_DIR 或 ASSISTANT_RERANKER_MODEL_DIR 未設定或目錄不存在；小幫手會顯示不可用。'
}

# ---------- 5. 快速通道 ----------
$Url = $null
$MarketUrl = $null
if ($NoTunnel) {
    Warn '未開對外網址（-NoTunnel）；服務只在 Docker 內部網路，模擬超市交接回 503。'
} else {
    Step '開啟 Cloudflare 快速通道（每次網址都不同）'
    Dc --profile tunnel up -d --force-recreate --no-deps cloudflared
    if ($LASTEXITCODE -ne 0) { Fail 'cloudflared 啟動失敗。' }
    $Url = Get-TunnelUrl 'overcooked-demo-cloudflared'
    if (-not $Url) { Fail '60 秒內沒有從 cloudflared 日誌取得網址：docker logs overcooked-demo-cloudflared' }
    & $Py -m ops.demo set --env-file .env.demo PUBLIC_HOSTNAME ($Url -replace '^https://', '')
    if (-not $SkipMarket) {
        Step '開啟模擬超市的快速通道（只轉到 sim-supermarket）'
        Dc --profile tunnel up -d --force-recreate --no-deps cloudflared-market
        if ($LASTEXITCODE -eq 0) { $MarketUrl = Get-TunnelUrl 'overcooked-demo-cloudflared-market' }
        if ($MarketUrl) {
            & $Py -m ops.demo set --env-file .env.demo SIM_MARKET_URL $MarketUrl
        } else {
            Warn '沒有取得模擬超市的快速通道網址（docker logs overcooked-demo-cloudflared-market）；採買交接會回 503。'
        }
    }
    Step '以新網址重建 backend（ALLOWED_ORIGINS、SIM_MARKET_URL）'
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
    if ($MarketUrl) { Write-Host "模擬超市：$MarketUrl" -ForegroundColor Green } else { Write-Host '模擬超市：未開放（交接回 503）' }
    Write-Host '（快速通道網址每次重開都會變；電腦關機或執行 scripts\demo-down.ps1 即離線）'
} else {
    Write-Host 'demo 服務已在本機啟動（未開對外網址）。' -ForegroundColor Green
}
Write-Host '關閉：.\scripts\demo-down.ps1（資料保留）'
