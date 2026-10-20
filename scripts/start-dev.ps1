<#
.SYNOPSIS
  一鍵啟動本機開發環境：資料庫、後端、worker、前端、YOLO 推論服務。

.DESCRIPTION
  每次執行都會自動同步，新增功能通常不用改這支腳本：
    - 沒有 .env.dev → 由 .env.dev.example 建立並產生隨機密碼
    - 沒有 .venv → 建立；requirements.txt 有變 → 重新安裝
    - package-lock.json 有變 → npm ci
    - 資料庫容器不存在 → 建立（資料存在具名 volume）；已存在 → 啟動
    - alembic upgrade head 套用新的 migration
    - 資料庫沒有食譜 → 從工作簿匯入
  每個服務開在自己的視窗；用 scripts/stop-dev.ps1 全部關閉。
  只有新增「獨立程序」（例如 T11 的知識 RAG 服務）時才需要修改這支腳本。

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
$Cfg = [ordered]@{}
foreach ($line in Get-Content $EnvFile -Encoding UTF8) {
    $t = $line.Trim()
    if ($t -eq '' -or $t.StartsWith('#') -or -not $t.Contains('=')) { continue }
    $i = $t.IndexOf('=')
    $Cfg[$t.Substring(0, $i).Trim()] = $t.Substring($i + 1).Trim().Trim('"')
}
function Cfg($key, $default = '') { if ($Cfg.Contains($key) -and $Cfg[$key] -ne '') { $Cfg[$key] } else { $default } }
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
$env:DATABASE_URL = "postgresql+psycopg://postgres:$DbPassword@127.0.0.1:$DbPort/$DbName"
$env:PYTHONPATH = "$Root;$Root\backend"
$env:PYTHONUTF8 = '1'
$DataRoot = Resolve-FromRoot (Cfg 'PROJECT_DATA_ROOT' '../AIPE04期末專題_data')
New-Item -ItemType Directory -Force $DataRoot | Out-Null
$env:PROJECT_DATA_ROOT = $DataRoot
# 其餘設定（例如之後新增的 API key）原樣傳下去；DEV_ 開頭與腳本專用的不傳
foreach ($k in $Cfg.Keys) {
    if ($k -like 'DEV_*' -or $k -in @('PROJECT_DATA_ROOT', 'YOLO_PYTHON', 'RECIPE_WORKBOOK', 'INFERENCE_WEIGHTS')) { continue }
    Set-Item "env:$k" $Cfg[$k]
}
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

# ---------- 7. 啟動各服務 ----------
$Running = @{}
if (Test-Path $PidFile) {
    foreach ($line in Get-Content $PidFile) {
        $name, $procId = $line -split '=', 2
        if (Get-Process -Id $procId -ErrorAction SilentlyContinue) { $Running[$name] = $procId }
    }
}
Set-Content $PidFile ($Running.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" })

function Start-Service-Window($name, $port, $command) {
    if ($Running.ContainsKey($name)) { Info "$name 已在執行，略過"; return }
    if ($port -and (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)) {
        Info "port $port 已被其他程式使用，略過 $name（可能已經開著）"; return
    }
    $p = Start-Process powershell -WorkingDirectory $Root -PassThru -ArgumentList @(
        '-NoExit', '-NoProfile', '-Command', "`$Host.UI.RawUI.WindowTitle = 'overcooked: $name'; $command")
    Add-Content $PidFile "$name=$($p.Id)"
    Info "$name 已啟動"
}

Step '啟動服務（每個服務一個視窗）'
Start-Service-Window 'backend' 8000 "& '$Py' -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000 --reload --reload-dir backend"
Start-Service-Window 'worker' $null "& '$Py' -m app.jobs.worker"
Start-Service-Window 'frontend' 5173 'npm --prefix frontend run dev -- --host 127.0.0.1 --port 5173 --strictPort'
if ($UseYolo) {
    Start-Service-Window 'yolo' $YoloPort "& '$YoloPy' inference/server.py"
} else {
    Write-Host '    YOLO 未啟動（DEV_SKIP_YOLO=1、套件安裝失敗，或 YOLO_PYTHON／INFERENCE_WEIGHTS 路徑不存在）；辨識會顯示不可用，仍可手動輸入食材。' -ForegroundColor Yellow
}

Step '等待後端就緒'
$up = $false
for ($i = 0; $i -lt 30; $i++) {
    try { Invoke-WebRequest 'http://127.0.0.1:8000/health' -UseBasicParsing -TimeoutSec 2 | Out-Null; $up = $true; break } catch { Start-Sleep -Seconds 2 }
}
if (-not $up) { Write-Host '    後端 60 秒內沒有回應，請看「overcooked: backend」視窗的錯誤訊息。' -ForegroundColor Yellow }

Write-Host ''
Write-Host '開發環境已啟動：http://localhost:5173' -ForegroundColor Green
Write-Host '關閉全部服務：雙擊 stop-dev.cmd，或執行 scripts\stop-dev.ps1'
if (-not $NoBrowser) { Start-Process 'http://localhost:5173' }
