# 地端 Qwen 產生最後小知識文字，並和雲端結果比較（在專案根目錄執行，Ollama 需已啟動並有該模型）
#   .\scripts\t11-explain-local.ps1                       # 預設 qwen3.5:9b-q4_K_M
#   .\scripts\t11-explain-local.ps1 -Model qwen3.5:9b-q4_K_M -Limit 10
param([string]$Model = 'qwen3.5:9b-q4_K_M', [int]$Limit = 0, [ValidateSet('off', 'on', 'default')][string]$Think = 'off')
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Py = if (Test-Path "$Root\.venv\Scripts\python.exe") { "$Root\.venv\Scripts\python.exe" } else { 'python' }
$run = @('scripts\t11_explain_benchmark.py', 'run', '--provider', 'ollama', '--model', $Model, '--think', $Think,
         '--out', 'outputs\t11_explain\local.jsonl')
if ($Limit -gt 0) { $run += @('--limit', $Limit) }
& $Py @run; if ($LASTEXITCODE) { throw 'local run failed' }
& $Py scripts\t11_explain_benchmark.py compare --run '雲端=outputs\t11_explain\cloud.jsonl' --run '地端=outputs\t11_explain\local.jsonl' --out 'outputs\t11_explain\report'
Write-Host '報告：outputs\t11_explain\report\report.md ；逐筆對照：outputs\t11_explain\report\comparison.csv'
