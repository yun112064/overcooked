<#
.SYNOPSIS
  一鍵啟動本機開發環境：資料庫、後端、worker、排程（含 T02 每日查價）、前端、知識 RAG、YOLO 推論服務。

.DESCRIPTION
  每次執行都會自動同步，新增功能通常不用改這支腳本：
    - 沒有 .env.dev → 由 .env.dev.example 建立並產生隨機密碼
    - 沒有 .venv → 建立；requirements.txt 有變 → 重新安裝
    - package-lock.json 有變 → npm ci
    - 資料庫容器不存在 → 建立（資料存在具名 volume）；已存在 → 啟動
    - alembic upgrade head 套用新的 migration
    - 資料庫沒有食譜 → 從工作簿匯入
    - .env.dev 缺 KNOWLEDGE_SERVICE_TOKEN／INTERNAL_API_TOKEN → 產生隨機值寫入（不顯示）
    - 知識庫 <DEV_DB_NAME>_knowledge 不存在 → 在同一個容器建立，並套用 knowledge_rag/schema.sql（可重複執行）
    - 今天的全聯查價（run key pxmart-daily-<日期>）還沒完成 → 背景補跑／續跑（T02 限速，約 12～15 分鐘）
  不需要的服務可在 .env.dev 設 DEV_SKIP_YOLO=1、DEV_SKIP_KNOWLEDGE=1、DEV_SKIP_PRICES=1 略過。
  一次性指令（查價、知識抓取／審核／建索引）用 scripts/dev-run.ps1，見 docs/knowledge-source-healthnews.md、docs/T02-pricing.md。
  所有服務都在同一個視窗執行，輸出以 [backend]、[frontend] 等前綴區分；
  在該視窗按 Ctrl+C 或執行 scripts/stop-dev.ps1 全部關閉。
  只有新增「獨立程序」時才需要修改這支腳本；共用的環境變數組裝在 scripts/dev-env.ps1。

.PARAMETER NoBrowser
  啟動完成後不要自動開瀏覽器。
#>
param([switch]$NoBrowser)

$ErrorActionPreference = 'Continue'  # 外部指令一律檢查 $LASTEXITCODE
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$StateDir = Join-Path $Root '.dev'
$PidFile = Join-Path $StateDir 'pids.txt'
New-Item -ItemType Directory -Force $StateDir | Out-Null

function Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Info($msg) { Write-Host "    $msg" }
function Fail($msg) { Write-Host "錯誤：$msg" -ForegroundColor Red; exit 1 }

# ---------- 1. 設定檔 ----------
$EnvFile = Join-Path $Root '.env.dev'
if (-not (Test-Path $EnvFile)) {
    Step '第一次使用：由 .env.dev.example 建立 .env.dev'
    $text = Get-Content (Join-Path $Root '.env.dev.example') -Raw -Encoding UTF8
    $text = $text.Replace('replace_with_random_password', [guid]::NewGuid().ToString('N'))
    $text = $text.Replace('replace_with_random_token', [guid]::NewGuid().ToString('N'))
    [IO.File]::WriteAllText($EnvFile, $text, (New-Object Text.UTF8Encoding $false))
    Info '已建立，可依自己電腦修改路徑（例如 YOLO 權重位置）。'
}
. (Join-Path $PSScriptRoot 'dev-env.ps1')
# 知識 RAG 與內部 API 的開發用 token：缺少或留空時產生隨機值寫回 .env.dev（只顯示鍵名）
$newKeys = Add-MissingDevSecrets $EnvFile @('KNOWLEDGE_SERVICE_TOKEN', 'INTERNAL_API_TOKEN')
if ($newKeys) { Info "已在 .env.dev 產生開發用 token：$($newKeys -join '、')（值不顯示）" }
$Cfg = Read-DevConfig $EnvFile
function Cfg($key, $default = '') { Get-DevValue $Cfg $key $default }
function Resolve-FromRoot($p) { if ([IO.Path]::IsPathRooted($p)) { $p } else { [IO.Path]::GetFullPath((Join-Path $Root $p)) } }

# ---------- 2. Python 虛擬環境 ----------
$Py = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Py)) {
    Step '建立 Python 虛擬環境 .venv（Python 3.12，與後端容器相同）'
    if (Get-Command py -ErrorAction SilentlyContinue) { & py -3.12 -m venv .venv } else { & python -m venv .venv }
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $Py)) { Fail '無法建立 .venv，請確認已安裝 Python 3.12。' }
}
$ReqStamp = Join-Path $Root '.venv\.requirements.sha256'
$ReqHash = (Get-FileHash (Join-Path $Root 'requirements.txt')).Hash
if (-not (Test-Path $ReqStamp) -or (Get-Content $ReqStamp) -ne $ReqHash) {
    Step '安裝後端套件（requirements.txt 有更新）'
    & $Py -m pip install --disable-pip-version-check -r requirements.txt
    if ($LASTEXITCODE -ne 0) { Fail 'pip install 失敗。' }
    Set-Content $ReqStamp $ReqHash
}

# ---------- 3. 前端套件 ----------
$LockStamp = Join-Path $Root 'frontend\node_modules\.overcooked-lock.sha256'
$LockHash = (Get-FileHash (Join-Path $Root 'frontend\package-lock.json')).Hash
if (-not (Test-Path $LockStamp) -or (Get-Content $LockStamp) -ne $LockHash) {
    Step '安裝前端套件（package-lock.json 有更新）'
    # npm ci 完全照 lock 檔安裝且不改寫它（npm install 會改 lock 檔，造成 Git 出現修改）
    & npm --prefix frontend ci
    if ($LASTEXITCODE -ne 0) { Fail 'npm ci 失敗，請確認已安裝 Node.js。' }
    Set-Content $LockStamp $LockHash
}

# ---------- 4. 資料庫 ----------
$Db = Cfg 'DEV_DB_CONTAINER' 'overcooked-dev-db'
$DbPort = Cfg 'DEV_DB_PORT' '55432'
$DbName = Cfg 'DEV_DB_NAME' 'meal_planner_dev'
$DbPassword = Cfg 'DEV_DB_PASSWORD'
if (-not $DbPassword) { Fail '.env.dev 缺少 DEV_DB_PASSWORD。' }

Step "啟動資料庫容器 $Db"
docker info *> $null
if ($LASTEXITCODE -ne 0) { Fail '連不到 Docker，請先開啟 Docker Desktop 再執行。' }
$exists = docker ps -a --filter "name=^$Db$" --format '{{.Names}}'
if (-not $exists) {
    docker run -d --name $Db -e "POSTGRES_PASSWORD=$DbPassword" -e "POSTGRES_DB=$DbName" `
        -p "127.0.0.1:${DbPort}:5432" -v "$(Cfg 'DEV_DB_VOLUME' 'overcooked-dev-pgdata'):/var/lib/postgresql/data" `
        pgvector/pgvector:0.8.6-pg17 | Out-Null
} else {
    docker start $Db | Out-Null
}
if ($LASTEXITCODE -ne 0) { Fail '資料庫容器啟動失敗。' }
$ready = $false
for ($i = 0; $i -lt 30; $i++) {
    docker exec $Db pg_isready -U postgres *> $null
    if ($LASTEXITCODE -eq 0) { $ready = $true; break }
    Start-Sleep -Seconds 2
}
if (-not $ready) { Fail '資料庫沒有在 60 秒內就緒。' }
$hasDb = docker exec $Db psql -U postgres -tAc "SELECT 1 FROM pg_database WHERE datname = '$DbName'"
if ("$hasDb".Trim() -ne '1') {
    docker exec $Db psql -U postgres -c "CREATE DATABASE $DbName" | Out-Null
}

# ---------- 5. 傳給各服務的環境變數 ----------
# DATABASE_URL、PYTHONPATH、其餘設定原樣傳下去，以及知識 RAG／內部 API 的連線（見 scripts/dev-env.ps1）
$DevEnv = Set-DevEnvironment $Cfg $Root
$Weights = Resolve-FromRoot (Cfg 'INFERENCE_WEIGHTS' 'inference/models/yolo26s-food41-denoise-seed45-50e.pt')
$env:INFERENCE_WEIGHTS = $Weights
$YoloPort = Cfg 'INFERENCE_PORT' '8765'
$YoloPy = Cfg 'YOLO_PYTHON'
if ((Cfg 'DEV_SKIP_YOLO') -eq '1') {
    $YoloPy = ''
} elseif (-not $YoloPy) {
    # 沒指定訓練環境 → 用專案內的 .venv-yolo（第一次會下載 Ultralytics 與 PyTorch，約數分鐘）
    $YoloPy = Join-Path $Root '.venv-yolo\Scripts\python.exe'
    if (-not (Test-Path $YoloPy)) {
        Step '建立 YOLO 推論環境 .venv-yolo'
        if (Get-Command py -ErrorAction SilentlyContinue) { & py -3.12 -m venv .venv-yolo } else { & python -m venv .venv-yolo }
    }
    $YoloStamp = Join-Path $Root '.venv-yolo\.requirements.sha256'
    $YoloHash = (Get-FileHash (Join-Path $Root 'inference\requirements.txt')).Hash
    if ((Test-Path $YoloPy) -and (-not (Test-Path $YoloStamp) -or (Get-Content $YoloStamp) -ne $YoloHash)) {
        Step '安裝 YOLO 推論套件（inference/requirements.txt 有更新；第一次較久）'
        & $YoloPy -m pip install --disable-pip-version-check -r inference/requirements.txt
        if ($LASTEXITCODE -eq 0) { Set-Content $YoloStamp $YoloHash } else { Write-Host '    YOLO 套件安裝失敗，先略過辨識服務。' -ForegroundColor Yellow; $YoloPy = '' }
    }
}
$UseYolo = $YoloPy -and (Test-Path $YoloPy) -and (Test-Path $Weights) -and (Cfg 'INFERENCE_TOKEN')
if ($UseYolo) {
    $env:RECOGNITION_SERVICE_URL = "http://127.0.0.1:$YoloPort"
    $env:RECOGNITION_SERVICE_TOKEN = Cfg 'INFERENCE_TOKEN'
}

# ---------- 6. 資料表與食譜 ----------
Step '套用資料庫 migration'
& $Py -m alembic -c alembic.ini upgrade head
if ($LASTEXITCODE -ne 0) { Fail 'alembic upgrade 失敗。' }

$count = docker exec $Db psql -U postgres -d $DbName -tAc 'SELECT count(*) FROM recipes'
if ("$count".Trim() -eq '0') {
    $Workbook = Resolve-FromRoot (Cfg 'RECIPE_WORKBOOK' 'data/recipe_dataset_1017.xlsx')
    if (Test-Path $Workbook) {
        Step '資料庫沒有食譜，從工作簿匯入（約一分鐘）'
        & $Py scripts/import_recipes.py --excel $Workbook --reviews config/recipe_reviews.json `
            --sources config/recipe_sources.json --report (Join-Path $StateDir 'import-report.json') | Out-Null
        if ($LASTEXITCODE -ne 0) { Fail '食譜匯入失敗，報告見 .dev\import-report.json。' }
    } else {
        Write-Host "    找不到食譜工作簿 $Workbook，先略過匯入（食材搜尋會是空的）。" -ForegroundColor Yellow
    }
}

# 資料種子（data/seed）：推估表為空時只提示，不自動匯入、不呼叫任何付費 API
$estimates = docker exec $Db psql -U postgres -d $DbName -tAc 'SELECT count(*) FROM conversion_factor_estimates'
if ("$estimates".Trim() -eq '0' -and (Test-Path (Join-Path $Root 'data\seed\manifest.json'))) {
    Write-Host '    推估表是空的：可匯入 repo 內的資料種子（推估、營養知識、價格快照，免費、冪等）：' -ForegroundColor Yellow
    Write-Host '      .\scripts\dev-run.ps1 -m app.jobs.import_seed_data' -ForegroundColor Yellow
    Write-Host '    詳見 README「首次啟動匯入資料種子」。' -ForegroundColor Yellow
}

# ---------- 6b. 知識資料庫（決策 #23：同一個 dev 容器內的 <DEV_DB_NAME>_knowledge） ----------
if (-not $DevEnv.SkipKnowledge) {
    $KnDb = $DevEnv.KnowledgeDbName
    if ($KnDb -notmatch '^[A-Za-z0-9_]+$') { Fail "知識庫名稱只能用英數與底線：$KnDb" }
    Step "準備知識資料庫 $KnDb"
    $hasKn = docker exec $Db psql -U postgres -tAc "SELECT 1 FROM pg_database WHERE datname = '$KnDb'"
    if ("$hasKn".Trim() -ne '1') {
        docker exec $Db psql -U postgres -c "CREATE DATABASE $KnDb" | Out-Null
    }
    # schema.sql 全部是 IF NOT EXISTS，可重複套用
    & $Py -c "import os; from knowledge_rag.store import apply_schema; apply_schema(os.environ['KNOWLEDGE_DATABASE_URL'])"
    if ($LASTEXITCODE -ne 0) {
        Write-Host '    知識庫 schema 套用失敗，先略過知識服務（知識說明會顯示不可用）。' -ForegroundColor Yellow
        $DevEnv.SkipKnowledge = $true
        Remove-Item env:KNOWLEDGE_SERVICE_URL -ErrorAction SilentlyContinue
    }
}

# ---------- 7. 啟動各服務 ----------
$Running = @{}
if (Test-Path $PidFile) {
    foreach ($line in Get-Content $PidFile) {
        $name, $procId = $line -split '=', 2
        if (Get-Process -Id $procId -ErrorAction SilentlyContinue) { $Running[$name] = $procId }
    }
}
Set-Content $PidFile ($Running.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" })

$env:PYTHONUNBUFFERED = '1'  # 輸出即時出現在同一個視窗
$Started = @()
function Start-Service-Here($name, $port, $command) {
    if ($Running.ContainsKey($name)) { Info "$name 已在執行，略過"; return }
    if ($port -and (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)) {
        Info "port $port 已被其他程式使用，略過 $name（可能已經開著）"; return
    }
    # 全部服務共用這個視窗，每行輸出前面加上服務名稱
    # 用 -EncodedCommand 傳指令，避免 Start-Process 把引號拿掉
    $full = "`$ProgressPreference = 'SilentlyContinue'; $command 2>&1 | ForEach-Object { '[$name] ' + `$_ }"
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($full))
    $p = Start-Process powershell -WorkingDirectory $Root -PassThru -NoNewWindow -ArgumentList @(
        '-NoProfile', '-EncodedCommand', $encoded)
    Add-Content $PidFile "$name=$($p.Id)"
    $script:Started += $p
    Info "$name 已啟動"
}

Step '啟動服務（全部在這個視窗，輸出以 [服務名稱] 區分）'
Start-Service-Here 'backend' 8000 "& '$Py' -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000 --reload --reload-dir backend"
Start-Service-Here 'worker' $null "& '$Py' -m app.jobs.worker"
if ($DevEnv.SkipKnowledge) {
    Write-Host '    知識 RAG 未啟動（DEV_SKIP_KNOWLEDGE=1 或知識庫無法使用）；知識說明會顯示不可用。' -ForegroundColor Yellow
} else {
    Start-Service-Here 'knowledge' $DevEnv.KnowledgePort "& '$Py' -m knowledge_rag.server"
}
# 單一排程程序（T10）：回收逾時工作、日誌清理、T02 每日 03:00 全聯查價
if ($DevEnv.SkipPrices) {
    Start-Service-Here 'scheduler' $null "`$env:PRICE_SCHEDULER_ENABLED = 'false'; & '$Py' -m app.jobs.scheduler"
    Write-Host '    價格收集未啟動（DEV_SKIP_PRICES=1）；成本會顯示缺價。' -ForegroundColor Yellow
} else {
    Start-Service-Here 'scheduler' $null "& '$Py' -m app.jobs.scheduler"
    # 開發機通常不在 03:00 開著：啟動時以「今天的排程 run key」補跑；已完成會立即略過，中斷過會續跑
    $Today = [TimeZoneInfo]::ConvertTimeBySystemTimeZoneId([DateTime]::UtcNow, 'Taipei Standard Time').ToString('yyyy-MM-dd')
    Start-Service-Here 'prices' $null "& '$Py' -m app.pricing_scheduler --run-once --run-key pxmart-daily-$Today"
}
Start-Service-Here 'frontend' 5173 'npm --prefix frontend run dev -- --host 127.0.0.1 --port 5173 --strictPort'
if ($UseYolo) {
    Start-Service-Here 'yolo' $YoloPort "& '$YoloPy' inference/server.py"
} else {
    Write-Host '    YOLO 未啟動（DEV_SKIP_YOLO=1、套件安裝失敗，或 YOLO_PYTHON／INFERENCE_WEIGHTS 路徑不存在）；辨識會顯示不可用，仍可手動輸入食材。' -ForegroundColor Yellow
}

Step '等待後端就緒'
$up = $false
for ($i = 0; $i -lt 30; $i++) {
    try { Invoke-WebRequest 'http://127.0.0.1:8000/health' -UseBasicParsing -TimeoutSec 2 | Out-Null; $up = $true; break } catch { Start-Sleep -Seconds 2 }
}
if (-not $up) { Write-Host '    後端 60 秒內沒有回應，請看上方 [backend] 開頭的錯誤訊息。' -ForegroundColor Yellow }

Write-Host ''
Write-Host '開發環境已啟動：http://localhost:5173' -ForegroundColor Green
if (-not $DevEnv.SkipKnowledge) {
    Write-Host "知識 RAG：http://127.0.0.1:$($DevEnv.KnowledgePort)（尚未抓取／審核／建索引前，知識說明仍顯示不可用；步驟見 docs/knowledge-source-healthnews.md）"
}
if (-not $DevEnv.SkipPrices) {
    Write-Host '全聯查價在背景執行（[prices]，T02 限速約 12～15 分鐘）；進度：.\scripts\dev-run.ps1 -m app.pricing_scheduler --show-runs 3'
}
Write-Host '關閉全部服務：在這個視窗按 Ctrl+C（或雙擊 stop-dev.cmd）'
if (-not $NoBrowser) { Start-Process 'http://localhost:5173' }
if (-not $Started) { exit 0 }  # 服務都已在別處執行，沒有要看守的輸出

# 留在這裡顯示各服務輸出；按 Ctrl+C 時一併關閉服務與資料庫容器（資料保留）
try {
    while ($Started | Where-Object { -not $_.HasExited }) { Start-Sleep -Seconds 1 }
    Write-Host '所有服務都已結束。' -ForegroundColor Yellow
} finally {
    & (Join-Path $PSScriptRoot 'stop-dev.ps1')
}
