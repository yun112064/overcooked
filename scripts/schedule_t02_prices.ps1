param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Install', 'Status', 'Disable')]
    [string]$Mode,
    [ValidatePattern('^([01]\d|2[0-3]):[0-5]\d$')]
    [string]$At = '03:00'
)

$ErrorActionPreference = 'Stop'
$taskName = 'Overcooked-T02-1102-V3'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$updateScript = Join-Path $PSScriptRoot 'update_t02_prices.py'
$taskArguments = '"{0}"' -f $updateScript
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
$pythonWindowless = Join-Path $repoRoot '.venv\Scripts\pythonw.exe'
$task = Get-ScheduledTask -ErrorAction Stop | Where-Object {
    $_.TaskPath -eq '\' -and $_.TaskName -eq $taskName
}
if ($task -and ($task.Actions.Count -ne 1 -or
        $task.Actions[0].Execute -ne $pythonWindowless -or $task.Actions[0].Arguments -ne $taskArguments)) {
    throw "The task name is already used by another command: $taskName"
}

if ($Mode -eq 'Disable') {
    if ($task) {
        Disable-ScheduledTask -InputObject $task | Out-Null
    }
    return
}
if ($Mode -eq 'Install') {
    if ((Get-TimeZone).Id -ne 'Taipei Standard Time') {
        throw 'Windows time zone must be Taipei Standard Time for this task.'
    }
    if (!(Test-Path -LiteralPath $python) -or !(Test-Path -LiteralPath $pythonWindowless)) {
        throw 'The project .venv is missing python.exe or pythonw.exe.'
    }
    & $python $updateScript --check
    if ($LASTEXITCODE -ne 0) {
        throw 'T02 preflight failed. See logs\t02-update.log.'
    }
    $action = New-ScheduledTaskAction -Execute $pythonWindowless -Argument $taskArguments -WorkingDirectory $repoRoot
    $trigger = New-ScheduledTaskTrigger -Daily -At ([datetime]::Today.Add([timespan]::Parse($At)))
    $userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 6)
    $task = Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
        -Principal $principal -Settings $settings -Description 'Refresh T02 prices in the dedicated local V3 database.' -Force
}
if (!$task) {
    Write-Output "$taskName is not registered."
    return
}
$info = Get-ScheduledTaskInfo -InputObject $task
[pscustomobject]@{
    TaskName = $taskName
    State = $task.State
    NextRunTime = $info.NextRunTime
    LastRunTime = $info.LastRunTime
    LastTaskResult = $info.LastTaskResult
}
