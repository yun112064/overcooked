<#
.SYNOPSIS
  一鍵啟動本機開發環境：資料庫、後端、worker、前端、YOLO 推論服務、T11 知識服務與 R14 飲食小幫手。

.DESCRIPTION
  每次執行都會自動同步，新增功能通常不用改這支腳本：
    - 沒有 .env.dev → 由 .env.dev.example 建立並產生隨機密碼
    - 沒有 .venv → 建立；requirements.txt 有變 → 重新安裝
    - package-lock.json 有變 → npm ci
    - 資料庫容器不存在 → 建立（資料存在具名 volume）；已存在 → 啟動
    - 僅檢查 V3 schema；資料遷移與來源安裝須明確執行
  所有服務都在同一個視窗執行，輸出以 [backend]、[frontend] 等前綴區分；
  在該視窗按 Ctrl+C 或執行 scripts/stop-dev.ps1 全部關閉。
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
$Db = Cfg 'DEV_DB_CONTAINER' 'overcooked-1102-v3-db'
$DbPort = Cfg 'DEV_DB_PORT' '55435'
$DbName = Cfg 'DEV_DB_NAME' 'meal_planner_1102_v3'
$DbPassword = Cfg 'DEV_DB_PASSWORD'
if (-not $DbPassword) { Fail '.env.dev 缺少 DEV_DB_PASSWORD。' }

Step "啟動資料庫容器 $Db"
docker info *> $null
if ($LASTEXITCODE -ne 0) { Fail '連不到 Docker，請先開啟 Docker Desktop 再執行。' }
$exists = docker ps -a --filter "name=^$Db$" --format '{{.Names}}'
if (-not $exists) {
    docker run -d --name $Db -e "POSTGRES_PASSWORD=$DbPassword" -e "POSTGRES_DB=$DbName" `
        -p "127.0.0.1:${DbPort}:5432" -v "$(Cfg 'DEV_DB_VOLUME' 'overcooked-1102-v3-pgdata'):/var/lib/postgresql/data" `
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
    if ($k -like 'DEV_*' -or $k -in @('PROJECT_DATA_ROOT', 'YOLO_PYTHON', 'RECIPE_WORKBOOK', 'NUTRITION_WORKBOOK', 'INFERENCE_WEIGHTS')) { continue }
    Set-Item "env:$k" $Cfg[$k]
}
$Weights = Resolve-FromRoot (Cfg 'INFERENCE_WEIGHTS' 'inference/models/yolo26s-food41-denoise-veg3-ft-20261108.pt')
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

# ---------- 6. 僅檢查既有資料表，不遷移、不匯入 ----------
Step '檢查資料庫 schema（1102_V3 資料整合待確認）'
& $Py -m app.core.schema_gate
if ($LASTEXITCODE -ne 0) { Fail '資料庫尚未準備完成。請先查閱 docs/1102_V3/DATABASE.md；此分支不會自動遷移或匯入。' }

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

# 後端放在自己的隱藏主控台，輸出寫到 .dev\<名稱>.out.log／.err.log，再由下面的 Show-ServiceLogs 印回這個視窗；
# 程式變更時由 Watch-CodeChanges 重新啟動它（不用 uvicorn --reload：它在 Windows 對子程序送 Ctrl+C，
# 會傳給同一主控台的所有服務，而且第二次以後常卡住）。
$LogTails = [ordered]@{}
function Start-Service-Isolated($name, $port, $exe, [string[]]$arguments) {
    # 獨立主控台的服務在關掉上次的視窗後不會跟著結束；它帶的是上次的環境變數，所以重新啟動
    if ($Running.ContainsKey($name)) {
        Info "$name 是上次啟動留下的，重新啟動以套用這次的設定"
        taskkill /T /F /PID $Running[$name] *> $null
        $Running.Remove($name)
        Set-Content $PidFile ($Running.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" })
        for ($i = 0; $i -lt 10 -and $port -and (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue); $i++) {
            Start-Sleep -Milliseconds 500
        }
    }
    if ($port -and (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)) {
        Info "port $port 已被其他程式使用，略過 $name（可能已經開著）"; return
    }
    $p = Start-Isolated-Process $name $exe $arguments
    Add-Content $PidFile "$name=$($p.Id)"
    $script:Started += $p
    $script:Reloadable[$name] = @{ Port = $port; Exe = $exe; Args = $arguments; Proc = $p }
    Info "$name 已啟動（獨立主控台；程式有變更時由本腳本重新啟動）"
}

function Start-Isolated-Process($name, $exe, [string[]]$arguments) {
    $out = Join-Path $StateDir "$name.out.log"
    $err = Join-Path $StateDir "$name.err.log"
    $p = Start-Process $exe -WorkingDirectory $Root -PassThru -WindowStyle Hidden -ArgumentList $arguments `
        -RedirectStandardOutput $out -RedirectStandardError $err
    # 重新啟動時記錄檔會被清空，從頭讀
    foreach ($f in @($out, $err)) { $script:LogTails[$f] = @{ Name = $name; Pos = 0L; Rest = '' } }
    return $p
}

# 自動重新載入：uvicorn --reload 在 Windows 靠對子程序送 Ctrl+C 重啟，第二次以後常收不到而卡住，
# 所以改由本腳本每 2 秒檢查 .py 檔，有變更就結束整個程序樹再啟動一次（不送 Ctrl+C）。
$Reloadable = @{}
$ReloadDirs = @('backend', 'nutrition_planning', 'nutrition_model', 'nutrition_target', 'knowledge_rag') |
    ForEach-Object { Join-Path $Root $_ } | Where-Object { Test-Path $_ }
function Get-CodeStamp {
    $files = @(Get-ChildItem -Path $ReloadDirs -Recurse -Filter '*.py' -File -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -notmatch '\\__pycache__\\' })
    $latest = ($files | ForEach-Object { $_.LastWriteTimeUtc.Ticks } | Measure-Object -Maximum).Maximum
    "$($files.Count):$latest"
}
$CodeStamp = $null
$LastCodeCheck = [DateTime]::MinValue
function Watch-CodeChanges {
    if (-not $script:Reloadable.Count -or ((Get-Date) - $script:LastCodeCheck).TotalSeconds -lt 2) { return }
    $script:LastCodeCheck = Get-Date
    $stamp = Get-CodeStamp
    if ($null -eq $script:CodeStamp) { $script:CodeStamp = $stamp; return }
    if ($stamp -eq $script:CodeStamp) { return }
    $script:CodeStamp = $stamp
    foreach ($name in @($script:Reloadable.Keys)) {
        $def = $script:Reloadable[$name]
        Write-Host "[$name] 偵測到程式變更，重新啟動…" -ForegroundColor Cyan
        taskkill /T /F /PID $def.Proc.Id *> $null
        for ($i = 0; $i -lt 20 -and $def.Port -and (Get-NetTCPConnection -LocalPort $def.Port -State Listen -ErrorAction SilentlyContinue); $i++) {
            Start-Sleep -Milliseconds 250
        }
        $old = $def.Proc
        $def.Proc = Start-Isolated-Process $name $def.Exe $def.Args
        $script:Started = @($script:Started | Where-Object { $_.Id -ne $old.Id }) + $def.Proc
        $lines = @(Get-Content $PidFile -ErrorAction SilentlyContinue | Where-Object { $_ -and -not $_.StartsWith("$name=") })
        Set-Content $PidFile ($lines + "$name=$($def.Proc.Id)")
    }
}

# 把獨立主控台服務的新輸出印到這個視窗，每行前面加上服務名稱
function Show-ServiceLogs {
    foreach ($path in @($script:LogTails.Keys)) {
        $tail = $script:LogTails[$path]
        if (-not (Test-Path $path)) { continue }
        try {
            $fs = [IO.File]::Open($path, 'Open', 'Read', 'ReadWrite')
            try {
                if ($fs.Length -le $tail.Pos) { continue }
                [void]$fs.Seek($tail.Pos, 'Begin')
                $buf = New-Object byte[] ($fs.Length - $tail.Pos)
                $read = $fs.Read($buf, 0, $buf.Length)
                $tail.Pos += $read
            } finally { $fs.Dispose() }
        } catch { continue }
        $text = $tail.Rest + [Text.Encoding]::UTF8.GetString($buf, 0, $read)
        $lines = $text -split "`r?`n"
        $tail.Rest = $lines[-1]  # 最後一段可能還沒寫完整行，留到下次
        if ($lines.Count -lt 2) { continue }
        foreach ($line in $lines[0..($lines.Count - 2)]) { if ($line) { Write-Host "[$($tail.Name)] $line" } }
    }
}

# ---------- 6b. T11 食材知識庫 ----------
# 沒有另外設定知識庫時，用同一個資料庫容器裡的獨立資料庫（768 維，與業務庫分開）。
# 每次啟動都核對 123 筆審核知識並在缺索引時用本機 embeddinggemma 建立；不下載模型、不呼叫付費 API。
if ((Cfg 'DEV_SKIP_KNOWLEDGE') -ne '1') {
    if (-not (Cfg 'KNOWLEDGE_DATABASE_URL')) {
        $KnowledgeDb = Cfg 'DEV_KNOWLEDGE_DB_NAME' 'overcooked_knowledge_dev'
        $hasKnowledgeDb = docker exec $Db psql -U postgres -tAc "SELECT 1 FROM pg_database WHERE datname = '$KnowledgeDb'"
        if ("$hasKnowledgeDb".Trim() -ne '1') {
            docker exec $Db psql -U postgres -c "CREATE DATABASE $KnowledgeDb" | Out-Null
        }
        $Cfg['KNOWLEDGE_DATABASE_URL'] = "postgresql://postgres:$DbPassword@127.0.0.1:$DbPort/$KnowledgeDb"
    }
    # 權杖只在本機後端與知識服務之間使用，未設定時每次啟動隨機產生
    # 存在 .dev\knowledge-token（已在 .gitignore），每次啟動沿用同一個，避免沿用中的後端與新的知識服務權杖不一致
    if (-not (Cfg 'KNOWLEDGE_SERVICE_TOKEN')) {
        $TokenFile = Join-Path $StateDir 'knowledge-token'
        if (-not (Test-Path $TokenFile)) { Set-Content $TokenFile ([guid]::NewGuid().ToString('N')) -NoNewline }
        $Cfg['KNOWLEDGE_SERVICE_TOKEN'] = (Get-Content $TokenFile -Raw).Trim()
    }
    $env:KNOWLEDGE_DATABASE_URL = $Cfg['KNOWLEDGE_DATABASE_URL']
    $env:KNOWLEDGE_SERVICE_TOKEN = $Cfg['KNOWLEDGE_SERVICE_TOKEN']
    Step '核對 T11 審核知識並檢查索引'
    $kb = & $Py -m knowledge_rag.bootstrap 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "    知識庫準備失敗，知識服務先不啟動：$($kb | Select-Object -Last 1)" -ForegroundColor Yellow
        $Cfg.Remove('KNOWLEDGE_DATABASE_URL'); Remove-Item env:KNOWLEDGE_DATABASE_URL -ErrorAction SilentlyContinue
    } else {
        Info ($kb | Select-Object -Last 1)
    }
}

# ---------- 6c. R14 飲食小幫手 ----------
# 需要 Data Root 的 assistant_rag\db.env（索引已建好）以及兩個既有容器：pgvector 資料庫與 TEI 重排服務。
# 這裡只啟動既有容器，不建立容器、不建索引、不下載模型；回答模型由本機 Ollama 提供（需自行開啟）。
# 缺任何一項就不啟動，小幫手頁會顯示離線。
$UseAssistant = $false
if ((Cfg 'DEV_SKIP_ASSISTANT') -ne '1') {
    $AssistantEnv = Join-Path $DataRoot 'assistant_rag\db.env'
    $AssistantContainers = @((Cfg 'DEV_ASSISTANT_DB_CONTAINER' 'overcooked-assistant-db'),
        (Cfg 'DEV_ASSISTANT_RERANKER_CONTAINER' 'overcooked-assistant-reranker'))
    $missing = @()
    if (-not (Test-Path $AssistantEnv) -and -not (Cfg 'ASSISTANT_DATABASE_URL')) { $missing += 'assistant_rag\db.env' }
    foreach ($c in $AssistantContainers) {
        if (-not (docker ps -a --filter "name=^$c$" --format '{{.Names}}')) { $missing += "容器 $c" }
    }
    if ($missing) {
        Write-Host "    飲食小幫手未啟動（缺少：$($missing -join '、')；建置方式見 assistant_rag/README.md）。" -ForegroundColor Yellow
    } else {
        Step '啟動飲食小幫手的資料庫與重排容器'
        foreach ($c in $AssistantContainers) { docker start $c | Out-Null }
        $AsStamp = Join-Path $Root '.venv\.assistant-requirements.sha256'
        $AsHash = (Get-FileHash (Join-Path $Root 'assistant_rag\requirements.txt')).Hash
        if (-not (Test-Path $AsStamp) -or (Get-Content $AsStamp) -ne $AsHash) {
            Step '安裝飲食小幫手套件（assistant_rag/requirements.txt 有更新）'
            & $Py -m pip install --disable-pip-version-check -r assistant_rag/requirements.txt
            if ($LASTEXITCODE -eq 0) { Set-Content $AsStamp $AsHash }
        }
        # 權杖只在本機後端與小幫手服務之間使用，存在 .dev\assistant-token（已在 .gitignore）
        if (-not (Cfg 'ASSISTANT_SERVICE_TOKEN')) {
            $AsTokenFile = Join-Path $StateDir 'assistant-token'
            if (-not (Test-Path $AsTokenFile)) { Set-Content $AsTokenFile ([guid]::NewGuid().ToString('N')) -NoNewline }
            $Cfg['ASSISTANT_SERVICE_TOKEN'] = (Get-Content $AsTokenFile -Raw).Trim()
        }
        $env:ASSISTANT_SERVICE_TOKEN = $Cfg['ASSISTANT_SERVICE_TOKEN']
        $AssistantPort = Cfg 'ASSISTANT_PORT' '8093'
        $env:ASSISTANT_SERVICE_URL = "http://127.0.0.1:$AssistantPort"
        $UseAssistant = $true
        # OpenAI 版小幫手（選用）：設定 ASSISTANT_OPENAI_PORT（例如 8094）才另外啟動一份。檢索仍在本機，
        # 回答經本機後端 /internal/assistant/chat 呼叫 OpenAI（T10 額度）；需要 .env.dev 的 OPENAI_API_KEY。
        $AssistantOpenAIPort = Cfg 'ASSISTANT_OPENAI_PORT'
        if ($AssistantOpenAIPort) {
            if (-not (Cfg 'OPENAI_API_KEY')) {
                Write-Host '    OpenAI 版小幫手：.env.dev 沒有 OPENAI_API_KEY，回答會失敗。' -ForegroundColor Yellow
            }
            # 內部 API 權杖只在本機後端與 OpenAI 版小幫手之間使用，存在 .dev\internal-token（已在 .gitignore）
            if (-not (Cfg 'INTERNAL_API_TOKEN')) {
                $InTokenFile = Join-Path $StateDir 'internal-token'
                if (-not (Test-Path $InTokenFile)) { Set-Content $InTokenFile ([guid]::NewGuid().ToString('N')) -NoNewline }
                $Cfg['INTERNAL_API_TOKEN'] = (Get-Content $InTokenFile -Raw).Trim()
            }
            $env:INTERNAL_API_TOKEN = $Cfg['INTERNAL_API_TOKEN']
            $env:ASSISTANT_OPENAI_SERVICE_URL = "http://127.0.0.1:$AssistantOpenAIPort"
        }
    }
}

Step '啟動服務（全部在這個視窗，輸出以 [服務名稱] 區分）'
if ((Cfg 'KNOWLEDGE_DATABASE_URL') -and (Cfg 'KNOWLEDGE_SERVICE_TOKEN')) {
    $KnowledgePort = Cfg 'KNOWLEDGE_PORT' '8091'
    $env:KNOWLEDGE_SERVICE_URL = "http://127.0.0.1:$KnowledgePort"
}
# 後端也會 import 這些專案根目錄的套件（T12 營養、T04、T03、T11 小知識）；改到它們也要自動重新載入
Start-Service-Isolated 'backend' 8000 $Py @('-m', 'uvicorn', 'app.main:app', '--app-dir', 'backend',
    '--host', '127.0.0.1', '--port', '8000')
Start-Service-Here 'worker' $null "& '$Py' -m app.jobs.worker"
Start-Service-Here 'frontend' 5173 'npm --prefix frontend run dev -- --host 127.0.0.1 --port 5173 --strictPort'
if ((Cfg 'KNOWLEDGE_DATABASE_URL') -and (Cfg 'KNOWLEDGE_SERVICE_TOKEN')) {
    Start-Service-Here 'knowledge' $KnowledgePort "& '$Py' -m uvicorn knowledge_rag.server:create_app --factory --host 127.0.0.1 --port $KnowledgePort"
} else {
    Write-Host '    食材知識服務未設定；知識結果會顯示不可用及缺少設定。' -ForegroundColor Yellow
}
if ($UseAssistant) {
    Start-Service-Here 'assistant' $AssistantPort "& '$Py' -m assistant_rag.server --port $AssistantPort"
    if ($AssistantOpenAIPort) {
        # 同一份程式、只換回答模型；權杖從環境變數帶入，不寫在指令列
        Start-Service-Here 'assistant-openai' $AssistantOpenAIPort ("`$env:RAG_LLM_PROVIDER = 'openai'; " +
            "`$env:CLOUD_INTERNAL_URL = 'http://127.0.0.1:8000'; `$env:CLOUD_INTERNAL_TOKEN = `$env:INTERNAL_API_TOKEN; " +
            "& '$Py' -m assistant_rag.server --port $AssistantOpenAIPort")
    }
}
if ($UseYolo) {
    Start-Service-Here 'yolo' $YoloPort "& '$YoloPy' inference/server.py"
} else {
    Write-Host '    YOLO 未啟動（DEV_SKIP_YOLO=1、套件安裝失敗，或 YOLO_PYTHON／INFERENCE_WEIGHTS 路徑不存在）；辨識會顯示不可用，仍可手動輸入食材。' -ForegroundColor Yellow
}

Step '等待後端就緒'
$up = $false
for ($i = 0; $i -lt 30; $i++) {
    try { Invoke-WebRequest 'http://127.0.0.1:8000/health' -UseBasicParsing -TimeoutSec 2 | Out-Null; $up = $true; break } catch { Show-ServiceLogs; Start-Sleep -Seconds 2 }
}
Show-ServiceLogs
if (-not $up) { Write-Host '    後端 60 秒內沒有回應，請看上方 [backend] 開頭的錯誤訊息。' -ForegroundColor Yellow }

Write-Host ''
Write-Host '開發環境已啟動：http://localhost:5173' -ForegroundColor Green
Write-Host '關閉全部服務：在這個視窗按 Ctrl+C（或雙擊 stop-dev.cmd）'
if (-not $NoBrowser) { Start-Process 'http://localhost:5173' }
if (-not $Started) { exit 0 }  # 服務都已在別處執行，沒有要看守的輸出

# 留在這裡顯示各服務輸出；按 Ctrl+C 時一併關閉服務與資料庫容器（資料保留）
try {
    while ($Started | Where-Object { -not $_.HasExited }) { Show-ServiceLogs; Watch-CodeChanges; Start-Sleep -Seconds 1 }
    Show-ServiceLogs
    Write-Host '所有服務都已結束。' -ForegroundColor Yellow
} finally {
    & (Join-Path $PSScriptRoot 'stop-dev.ps1')
}
