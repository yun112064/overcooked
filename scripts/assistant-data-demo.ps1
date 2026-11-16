<#
.SYNOPSIS
  飲食小幫手（R14）：為單機 demo 爬取語料並建索引。第一次約 1–2 小時；可中斷，重跑會接續。
.DESCRIPTION
  scripts/assistant-data.ps1 的 demo 版：在 overcooked-demo 專案的 rag-tools 容器內執行
  （compose.demo.yaml，profile assistant＋assistant-tools），原文寫在 .env.demo 的 ASSISTANT_DATA_DIR（Git 之外的主機目錄）。
  爬取是操作者明確執行的動作；demo-up 不會自動爬取。
    1. 爬取健康醫療網「營養飲食」與 Hello醫師「健康飲食」：assistant_rag 強制遵守 robots.txt、每個來源單一連線、
       每次請求間隔 ≥2 秒、可識別 User-Agent（非商業專題）；兩個來源同時進行。
    2. python -m assistant_rag.index build：分段、jieba 斷詞、embeddinggemma 向量
       （主機 Ollama，不呼叫付費 API）；套用 assistant_rag/exclusions.json 已核准的排除名單。
  建好後不必重啟服務，下一次提問即使用新索引。需要先執行過一次 scripts\demo-up.ps1（產生 .env.demo）；映像由本腳本建置（已建好時走快取）。
.PARAMETER SkipCrawl
  只建索引（語料已在 ASSISTANT_DATA_DIR）。
#>
param([switch]$SkipCrawl)
$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$StateDir = Join-Path $Root '.dev'
New-Item -ItemType Directory -Force $StateDir | Out-Null
. (Join-Path $PSScriptRoot 'dev-env.ps1')  # Read-DevConfig、Get-DevValue
$Compose = @('compose', '-p', 'overcooked-demo', '--env-file', '.env.demo', '-f', 'compose.yaml', '-f', 'compose.demo.yaml',
    '--profile', 'assistant', '--profile', 'assistant-tools')
function Dc { docker @Compose @args }
function Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host "錯誤：$msg" -ForegroundColor Red; exit 1 }

$EnvFile = Join-Path $Root '.env.demo'
if (-not (Test-Path $EnvFile)) { Fail '找不到 .env.demo（先執行 scripts\demo-up.ps1）。' }
$Demo = Read-DevConfig $EnvFile
$DataDir = Get-DevValue $Demo 'ASSISTANT_DATA_DIR'
if (-not $DataDir -or -not (Test-Path $DataDir -PathType Container)) {
    Fail '.env.demo 的 ASSISTANT_DATA_DIR 未設定或目錄不存在（放在 Git 之外的主機目錄，先建立再執行）。'
}
if (-not (Get-DevValue $Demo 'ASSISTANT_DB_PASSWORD')) { Fail '.env.demo 沒有 ASSISTANT_DB_PASSWORD（先執行一次 scripts\demo-up.ps1 補齊）。' }
docker info *> $null
if ($LASTEXITCODE -ne 0) { Fail '連不到 Docker，請先開啟 Docker Desktop。' }

# rag-tools 只引用映像、沒有 build 區：demo-up 帶 -SkipAssistant（或當時語料目錄還沒建立）時映像不存在，先建置（已建好時走快取）
Step '建置飲食小幫手映像（overcooked-demo-assistant-rag）'
Dc build assistant-rag
if ($LASTEXITCODE -ne 0) { Fail '飲食小幫手映像建置失敗。' }

Step '啟動飲食小幫手資料庫（overcooked-demo-assistant-db）'
Dc up -d --wait assistant-db
if ($LASTEXITCODE -ne 0) { Fail 'assistant-db 啟動失敗。' }

if (-not $SkipCrawl) {
    Step '爬取語料（兩個來源同時進行；日誌 .dev\assistant-crawl-demo-*.log）'
    $jobs = foreach ($source in 'healthnews', 'helloyishi') {
        $log = Join-Path $StateDir "assistant-crawl-demo-$source.log"
        Start-Process -FilePath docker -PassThru -WindowStyle Hidden -RedirectStandardOutput $log `
            -RedirectStandardError (Join-Path $StateDir "assistant-crawl-demo-$source.err.log") `
            -ArgumentList (@($Compose) + @('run', '--rm', '-e', 'PYTHONUNBUFFERED=1', 'rag-tools', 'python', '-m',
                                          'assistant_rag.crawl', $source))
    }
    $jobs | Wait-Process
    $failed = $jobs | Where-Object { $_.ExitCode -ne 0 }
    if ($failed) { Fail '爬取中斷（重跑本腳本會接續；看 .dev\assistant-crawl-demo-*.err.log）。' }
    Dc run --rm rag-tools python -m assistant_rag.crawl stats
}

Step '建索引（分段、斷詞、embeddinggemma 向量；可中斷接續）'
Dc run --rm rag-tools python -m assistant_rag.index build
if ($LASTEXITCODE -ne 0) { Fail '建索引失敗（確認主機 Ollama 已開且有 embeddinggemma）。' }
Dc run --rm rag-tools python -m assistant_rag.index status
Write-Host '飲食小幫手索引已建立；不必重啟，下一次提問即生效。' -ForegroundColor Green
