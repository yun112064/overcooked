<#
.SYNOPSIS
  在 Windows 工作排程器註冊（或移除）T17 正式環境的兩個工作：
    MealPlanner-Backup   每日 03:30 執行 scripts\backup-daily.ps1（錯過時開機後補跑）
    MealPlanner-Start    登入時執行 scripts\prod-up.ps1（Docker Desktop 需設為開機自動啟動）
.DESCRIPTION
  以目前使用者身分、只在登入時執行，不儲存密碼。容器本身已設 restart: unless-stopped，
  這裡負責重開機後啟動 YOLO、知識與小幫手原生程序並確認主系統在跑。
.PARAMETER Remove
  移除這兩個工作。
#>
param([switch]$Remove)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Names = 'MealPlanner-Backup', 'MealPlanner-Start'

if ($Remove) {
    foreach ($n in $Names) { Unregister-ScheduledTask -TaskName $n -Confirm:$false -ErrorAction SilentlyContinue }
    Write-Host '已移除 MealPlanner-Backup、MealPlanner-Start。'
    exit 0
}

function Action($script) {
    New-ScheduledTaskAction -Execute 'powershell.exe' -WorkingDirectory $Root `
        -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$(Join-Path $Root "scripts\$script")`""
}
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)
$user = "$env:USERDOMAIN\$env:USERNAME"
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive

Register-ScheduledTask -TaskName 'MealPlanner-Backup' -Force -Action (Action 'backup-daily.ps1') `
    -Trigger (New-ScheduledTaskTrigger -Daily -At '03:30') -Settings $settings -Principal $principal `
    -Description 'T17 每日備份（業務庫、知識庫、飲食小幫手庫），含獨立位置副本與 7 日保留' | Out-Null
$logon = New-ScheduledTaskTrigger -AtLogOn -User $user
$logon.Delay = 'PT2M'  # 等 Docker Desktop 起來
Register-ScheduledTask -TaskName 'MealPlanner-Start' -Force -Action (Action 'prod-up.ps1') `
    -Trigger $logon -Settings $settings -Principal $principal `
    -Description 'T17 正式環境：登入後啟動 YOLO、知識、飲食小幫手與主系統' | Out-Null
Write-Host '已註冊 MealPlanner-Backup（每日 03:30）與 MealPlanner-Start（登入後 2 分鐘）。' -ForegroundColor Green
Write-Host '查看：Get-ScheduledTask MealPlanner-*；移除：.\scripts\install-prod-tasks.ps1 -Remove'
