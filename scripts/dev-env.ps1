<#
.SYNOPSIS
  start-dev.ps1 與 dev-run.ps1 共用：讀 .env.dev、補產生開發用 token、組出各服務的環境變數。

.DESCRIPTION
  以 dot-source 載入（. scripts/dev-env.ps1），只定義函式，不做任何事。
  秘密值只寫進 .env.dev 與子程序環境，絕不輸出到畫面。
#>

# 讀 .env.dev → 有序字典；同一個鍵出現多次時，後面「非空」的值優先
function Read-DevConfig([string]$EnvFile) {
    $cfg = [ordered]@{}
    foreach ($line in Get-Content $EnvFile -Encoding UTF8) {
        $t = $line.Trim()
        if ($t -eq '' -or $t.StartsWith('#') -or -not $t.Contains('=')) { continue }
        $i = $t.IndexOf('=')
        $key = $t.Substring(0, $i).Trim()
        $value = $t.Substring($i + 1).Trim().Trim('"')
        if ($value -ne '' -or -not $cfg.Contains($key)) { $cfg[$key] = $value }
    }
    $cfg
}

function New-DevSecret {
    $bytes = New-Object byte[] 32
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    -join ($bytes | ForEach-Object { $_.ToString('x2') })  # 64 個十六進位字元
}

# 缺少或留空的 token 補上隨機值並寫回 .env.dev；只回報鍵名，不輸出值
function Add-MissingDevSecrets([string]$EnvFile, [string[]]$Keys) {
    $lines = [Collections.Generic.List[string]]@(Get-Content $EnvFile -Encoding UTF8)
    $cfg = Read-DevConfig $EnvFile
    $added = @()
    foreach ($key in $Keys) {
        if ($cfg.Contains($key) -and $cfg[$key] -ne '') { continue }
        $secret = New-DevSecret
        $idx = -1
        for ($n = 0; $n -lt $lines.Count; $n++) {
            if ($lines[$n] -match "^\s*$([regex]::Escape($key))\s*=") { $idx = $n; break }
        }
        if ($idx -ge 0) { $lines[$idx] = "$key=$secret" } else { $lines.Add("$key=$secret") }
        $added += $key
    }
    if ($added) {
        $text = ($lines -join "`r`n") + "`r`n"
        [IO.File]::WriteAllText($EnvFile, $text, (New-Object Text.UTF8Encoding $false))
    }
    $added
}

function Get-DevValue($Cfg, $key, $default = '') { if ($Cfg.Contains($key) -and $Cfg[$key] -ne '') { $Cfg[$key] } else { $default } }

# 設定目前程序的環境變數（子程序繼承）；回傳之後步驟需要的值
function Set-DevEnvironment($Cfg, [string]$Root) {
    $dbPort = Get-DevValue $Cfg 'DEV_DB_PORT' '55432'
    $dbName = Get-DevValue $Cfg 'DEV_DB_NAME' 'meal_planner_dev'
    $dbPassword = Get-DevValue $Cfg 'DEV_DB_PASSWORD'
    $knDbName = Get-DevValue $Cfg 'DEV_KNOWLEDGE_DB_NAME' "${dbName}_knowledge"
    $knPort = Get-DevValue $Cfg 'KNOWLEDGE_PORT' '8766'
    $skipKnowledge = (Get-DevValue $Cfg 'DEV_SKIP_KNOWLEDGE') -eq '1'

    $env:DATABASE_URL = "postgresql+psycopg://postgres:$dbPassword@127.0.0.1:$dbPort/$dbName"
    $env:PYTHONPATH = "$Root;$Root\backend"
    $env:PYTHONUTF8 = '1'
    $dataRoot = Get-DevValue $Cfg 'PROJECT_DATA_ROOT' '../AIPE04期末專題_data'
    if (-not [IO.Path]::IsPathRooted($dataRoot)) { $dataRoot = [IO.Path]::GetFullPath((Join-Path $Root $dataRoot)) }
    New-Item -ItemType Directory -Force $dataRoot | Out-Null
    $env:PROJECT_DATA_ROOT = $dataRoot
    # 其餘設定（例如之後新增的 API key）原樣傳下去；DEV_ 開頭與腳本專用的不傳
    foreach ($k in $Cfg.Keys) {
        if ($k -like 'DEV_*' -or $k -in @('PROJECT_DATA_ROOT', 'YOLO_PYTHON', 'RECIPE_WORKBOOK', 'INFERENCE_WEIGHTS')) { continue }
        Set-Item "env:$k" $Cfg[$k]
    }
    # 僅限開發：knowledge_rag.admin approve-all --dev-bulk 只在 APP_ENV=development 時執行（決策 #24）
    $env:APP_ENV = 'development'

    # 知識 RAG（決策 #23）：同一個 dev 容器內的 <DEV_DB_NAME>_knowledge
    $env:KNOWLEDGE_DATABASE_URL = "postgresql://postgres:$dbPassword@127.0.0.1:$dbPort/$knDbName"
    $env:KNOWLEDGE_TOKEN = Get-DevValue $Cfg 'KNOWLEDGE_SERVICE_TOKEN'
    $env:KNOWLEDGE_HOST = '127.0.0.1'
    $env:KNOWLEDGE_PORT = $knPort
    if ($skipKnowledge) {
        Remove-Item env:KNOWLEDGE_SERVICE_URL -ErrorAction SilentlyContinue
    } elseif (-not (Get-DevValue $Cfg 'KNOWLEDGE_SERVICE_URL')) {
        $env:KNOWLEDGE_SERVICE_URL = "http://127.0.0.1:$knPort"
    }
    # 知識管理指令（crawl --queued、index build、status）把本機後端當成「雲端內部 API」
    $env:CLOUD_INTERNAL_URL = Get-DevValue $Cfg 'CLOUD_INTERNAL_URL' 'http://127.0.0.1:8000'
    $env:CLOUD_INTERNAL_TOKEN = Get-DevValue $Cfg 'INTERNAL_API_TOKEN'

    [pscustomobject]@{
        DbPort = $dbPort; DbName = $dbName; DbPassword = $dbPassword
        KnowledgeDbName = $knDbName; KnowledgePort = $knPort; SkipKnowledge = $skipKnowledge
        SkipPrices = (Get-DevValue $Cfg 'DEV_SKIP_PRICES') -eq '1'
    }
}
