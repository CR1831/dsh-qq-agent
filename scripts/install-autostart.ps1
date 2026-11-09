#requires -Version 5.1
<#
Register a Startup shortcut for the QQ service.  The shortcut runs pythonw.exe
with the venv, so there is no console window; logs go to <home>\logs\service.log.
#>
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Service = Join-Path $Root 'service'
$HomeDir = if ($env:QQ_AGENT_HOME) { $env:QQ_AGENT_HOME } else { Join-Path $env:USERPROFILE '.dsh\qq-agent' }
$env:QQ_AGENT_HOME = $HomeDir

$Py = Join-Path $Service '.venv\Scripts\pythonw.exe'
if (-not (Test-Path $Py)) { throw "venv pythonw not found: $Py. Run scripts\install.ps1 first." }

$DefaultHome = Join-Path $env:USERPROFILE '.dsh\qq-agent'
if ($HomeDir -ne $DefaultHome) {
    # The shortcut cannot carry environment variables; make the custom home
    # visible to the login session instead.
    [Environment]::SetEnvironmentVariable('QQ_AGENT_HOME', $HomeDir, 'User')
    Write-Host "Set user env QQ_AGENT_HOME=$HomeDir"
}

$Startup = [Environment]::GetFolderPath('Startup')
$Link = Join-Path $Startup 'qq-agent.lnk'
$Shell = New-Object -ComObject WScript.Shell
$Shortcut = $Shell.CreateShortcut($Link)
$Shortcut.TargetPath = $Py
$Shortcut.Arguments = '-m qq_agent qq --listen --log-file "' + (Join-Path $HomeDir 'logs\service.log') + '"'
$Shortcut.WorkingDirectory = $Service
$Shortcut.WindowStyle = 1
$Shortcut.Description = 'dsh-qq-agent'
$Shortcut.Save()

Write-Host "Autostart shortcut created: $Link"
Write-Host "Logs: $(Join-Path $HomeDir 'logs\service.log')"
