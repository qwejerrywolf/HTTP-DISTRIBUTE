param([Parameter(Mandatory=$true)][string]$ProjectPath)
$ErrorActionPreference = 'Stop'
$project = (Resolve-Path -LiteralPath $ProjectPath).Path
$pythonExe = Join-Path $project '.venv\Scripts\python.exe'
$entryPoint = Join-Path $project 'main.py'
if (-not (Test-Path -LiteralPath $pythonExe) -or -not (Test-Path -LiteralPath $entryPoint)) {
    throw '项目或虚拟环境不存在，请先完成 README 安装和管理员初始化。'
}
$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $pythonExe -Argument ('"' + $entryPoint + '"') -WorkingDirectory $project
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $currentUser
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName 'HTTPFileDelivery' -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description 'Windows HTTP file delivery; single supervised Python process'
Write-Host '已登记当前用户登录任务；尚未启动。可在任务计划程序中检查并启动。'
