<#
.SYNOPSIS
  飲食小幫手（R14）：爬取語料並在正式環境建索引。第一次約 1–2 小時；可中斷，重跑會接續。
.DESCRIPTION
  在 rag-tools 容器內執行（compose.prod.yaml，profile tools），原文寫在 .env.prod 的 ASSISTANT_DATA_DIR（Git 之外）：
    1. 爬取健康醫療網「營養飲食」與 Hello醫師「健康飲食」：遵守 robots.txt、每個來源單一連線、
       每次請求間隔 ≥2 秒、可識別 User-Agent（非商業專題）；兩個來源同時進行。
    2. python -m assistant_rag.index build：分段、jieba 斷詞、embeddinggemma 向量
       （主機 Ollama，不呼叫付費 API）；套用 assistant_rag/exclusions.json 已核准的排除名單。
  建好後不必重啟服務，下一次提問即使用新索引。需要先執行過一次 scripts\prod-up.ps1。
.PARAMETER SkipCrawl
  只建索引（語料已在 ASSISTANT_DATA_DIR）。
#>
param([switch]$SkipCrawl)
$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$StateDir = Join-Path $Root '.dev'
New-Item -ItemType Directory -Force $StateDir | Out-Null
$Compose = @('compose', '--env-file', '.env.prod', '-f', 'compose.yaml', '-f', 'compose.prod.yaml')
function Dc { docker @Compose @args }
function Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host "錯誤：$msg" -ForegroundColor Red; exit 1 }

if (-not (Test-Path (Join-Path $Root '.env.prod'))) { Fail '找不到 .env.prod（先執行 scripts\prod-up.ps1）。' }
docker info *> $null
if ($LASTEXITCODE -ne 0) { Fail '連不到 Docker，請先開啟 Docker Desktop。' }

if (-not $SkipCrawl) {
    Step '爬取語料（兩個來源同時進行；日誌 .dev\assistant-crawl-*.log）'
    $jobs = foreach ($source in 'healthnews', 'helloyishi') {
        $log = Join-Path $StateDir "assistant-crawl-$source.log"
        Start-Process -FilePath docker -PassThru -WindowStyle Hidden -RedirectStandardOutput $log `
            -RedirectStandardError (Join-Path $StateDir "assistant-crawl-$source.err.log") `
            -ArgumentList (@($Compose) + @('run', '--rm', '-e', 'PYTHONUNBUFFERED=1', 'rag-tools', 'python', '-m',
                                          'assistant_rag.crawl', $source))
    }
    $jobs | Wait-Process
    $failed = $jobs | Where-Object { $_.ExitCode -ne 0 }
    if ($failed) { Fail '爬取中斷（重跑本腳本會接續；看 .dev\assistant-crawl-*.err.log）。' }
    Dc run --rm rag-tools python -m assistant_rag.crawl stats
}

Step '建索引（分段、斷詞、embeddinggemma 向量；可中斷接續）'
Dc run --rm rag-tools python -m assistant_rag.index build
if ($LASTEXITCODE -ne 0) { Fail '建索引失敗（確認 Ollama 已開且有 embeddinggemma）。' }
Dc run --rm rag-tools python -m assistant_rag.index status
Write-Host '飲食小幫手索引已建立；不必重啟，下一次提問即生效。' -ForegroundColor Green
