<#
.SYNOPSIS
  T17 展示備援：在這台電腦以 Docker 啟動整套系統，經 Cloudflare 快速通道（trycloudflare.com 臨時網址）對外。
.DESCRIPTION
  正式部署請用 compose.prod.yaml（named tunnel＋自己的網域）。快速通道的限制：網址每次重開都會變、
  電腦關機整站離線、不支援 SSE（飲食小幫手的串流回答送不出去）、沒有可用性保證。

  每次執行都可重複（已完成的步驟會略過）：
    - 沒有 .env.standby → 產生（隨機 DB 密碼與內部權杖；OPENAI_API_KEY 等由 .env.dev 帶入；不顯示任何秘密）
    - 建置映像 → 啟動 db → 第一次以 install_real_nutrition 安裝經雜湊核對的真實食譜／營養資料包 → alembic upgrade head
    - YOLO 以主機原生 .venv-yolo 執行（GPU），只綁 127.0.0.1；容器經 host.docker.internal 連線
    - 啟動其餘服務與 cloudflared，從日誌取得 https://*.trycloudflare.com，寫入 PUBLIC_HOSTNAME 後重建 backend
  主機不開任何連入埠；只有 frontend 經 cloudflared 對外，/api/internal/* 對外一律 404。
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

$Compose = @('compose', '-p', 'overcooked-standby', '--env-file', '.env.standby', '-f', 'compose.yaml', '-f', 'compose.standby.yaml')
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
Step '準備 .env.standby'
& $Py -m ops.standby init-env --env-file .env.standby --dev-env .env.dev
if ($LASTEXITCODE -ne 0) { Fail '無法建立 .env.standby。' }
$Demo = Read-EnvFile (Join-Path $Root '.env.standby')
$Dev = Read-EnvFile (Join-Path $Root '.env.dev')

# ---------- 2. 映像與資料庫 ----------
Step '建置映像（第一次約數分鐘）'
Dc build backend frontend
if ($LASTEXITCODE -ne 0) { Fail '映像建置失敗。' }
Step '啟動資料庫（overcooked-standby-db；資料在 demo 專用 volume）'
Dc up -d --wait db
if ($LASTEXITCODE -ne 0) { Fail '資料庫容器啟動失敗。' }

if (-not (Val $Demo 'DATABASE_URL')) {
    $DbEnv = Join-Path $StateDir 'standby-db.env'
    if (-not (Test-Path $DbEnv)) {
        $DemoDb = Val $Demo 'STANDBY_DATABASE' 'meal_planner_standby'
        Step "第一次：安裝真實食譜／營養資料包到新資料庫 $DemoDb（核對雜湊，約一分鐘）"
        # 管理連線只從環境變數帶入容器，不出現在指令列
        $env:INSTALL_DATABASE_ADMIN_URL = "postgresql+psycopg://postgres:$($Demo['POSTGRES_PASSWORD'])@db:5432/postgres"
        Dc run --rm --no-deps -w /app -e INSTALL_DATABASE_ADMIN_URL -v "${StateDir}:/app/.test-runtime" backend `
            python scripts/install_real_nutrition.py --database $DemoDb --output-env /app/.test-runtime/standby-db.env
        $code = $LASTEXITCODE
        Remove-Item env:INSTALL_DATABASE_ADMIN_URL -ErrorAction SilentlyContinue
        if ($code -ne 0) { Fail '資料包安裝失敗（新資料庫保留供診斷；改 .env.standby 的 STANDBY_DATABASE 換新名稱再試）。' }
    }
    & $Py -m ops.standby merge --env-file .env.standby --from .dev/standby-db.env
    if ($LASTEXITCODE -ne 0) { Fail '無法帶入資料庫設定。' }
    $Demo = Read-EnvFile (Join-Path $Root '.env.standby')
}
Step '套用資料庫 migration（alembic upgrade head；用管理連線，應用程式角色沒有 DDL 權限）'
$env:DATABASE_URL = "postgresql+psycopg://postgres:$($Demo['POSTGRES_PASSWORD'])@db:5432/$(Val $Demo 'STANDBY_DATABASE' 'meal_planner_standby')"
Dc run --rm --no-deps -e DATABASE_URL backend alembic -c /app/alembic.ini upgrade head
$code = $LASTEXITCODE
Remove-Item env:DATABASE_URL -ErrorAction SilentlyContinue
if ($code -ne 0) { Fail 'alembic upgrade 失敗。' }

# ---------- 3. YOLO（主機原生，GPU） ----------
$YoloPidFile = Join-Path $StateDir 'standby-yolo.pid'
$YoloPort = Val $Demo 'STANDBY_YOLO_PORT' '8776'
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
        Warn "port $YoloPort 已被其他程式使用，略過 YOLO（可在 .env.standby 改 STANDBY_YOLO_PORT）。"
    } else {
        Step "啟動 YOLO（127.0.0.1:$YoloPort，日誌 .dev\standby-yolo.log）"
        $env:INFERENCE_TOKEN = $Demo['INFERENCE_TOKEN']
        $env:INFERENCE_WEIGHTS = $Weights
        $env:INFERENCE_HOST = '127.0.0.1'
        $env:INFERENCE_PORT = $YoloPort
        $env:INFERENCE_DEVICE = Val $Demo 'INFERENCE_DEVICE' (Val $Dev 'INFERENCE_DEVICE' 'cpu')
        $p = Start-Process -FilePath $YoloPy -ArgumentList 'inference/server.py' -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $StateDir 'standby-yolo.log') -RedirectStandardError (Join-Path $StateDir 'standby-yolo.err.log')
        Set-Content $YoloPidFile $p.Id
        Remove-Item env:INFERENCE_TOKEN -ErrorAction SilentlyContinue
    }
}
if (-not (Val $Demo 'KNOWLEDGE_SERVICE_URL')) { Warn 'KNOWLEDGE_SERVICE_URL 未設定：知識說明會顯示不可用。' }
if (-not (Val $Demo 'ASSISTANT_OPENAI_SERVICE_URL')) { Warn 'ASSISTANT_OPENAI_SERVICE_URL 未設定：飲食小幫手會顯示不可用。' }

# ---------- 4. 服務與快速通道 ----------
Step '啟動 backend、worker、scheduler、frontend'
Dc up -d --wait backend worker scheduler frontend
if ($LASTEXITCODE -ne 0) { Fail '服務啟動失敗（docker compose -p overcooked-standby logs backend 查看原因）。' }
if ($NoTunnel) {
    Info '未開對外網址（-NoTunnel）；本機沒有發佈任何連入埠。'
    exit 0
}
Step '啟動 Cloudflare 快速通道'
Dc --profile tunnel up -d --force-recreate cloudflared
if ($LASTEXITCODE -ne 0) { Fail 'cloudflared 啟動失敗。' }
$url = $null
for ($i = 0; $i -lt 30 -and -not $url; $i++) {
    Start-Sleep -Seconds 2
    $url = (Dc --profile tunnel logs cloudflared 2>&1 | Out-String) | & $Py -m ops.standby tunnel-url
}
if (-not $url) { Fail '30 秒內沒有取得 trycloudflare.com 網址（docker compose -p overcooked-standby logs cloudflared）。' }
$hostname = ([Uri]$url).Host
& $Py -m ops.standby set --env-file .env.standby PUBLIC_HOSTNAME $hostname
Step '以新網址重建 backend（ALLOWED_ORIGINS）'
Dc up -d --wait --force-recreate backend
if ($LASTEXITCODE -ne 0) { Fail 'backend 重建失敗。' }
Write-Host ''
Write-Host "展示網址：$url" -ForegroundColor Green
Warn '快速通道只供展示：網址每次重開都會變、飲食小幫手串流回答無法經快速通道送出、電腦關機即離線。'
Info '關閉：.\scripts\standby-down.ps1（資料保留）'
