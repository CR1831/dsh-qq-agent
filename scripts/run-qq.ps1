#requires -Version 5.1
<#
Run the QQ service in the foreground, with the runtime home resolved the same
way as the plugin: QQ_AGENT_HOME, else %USERPROFILE%\.dsh\qq-agent.
#>
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Service = Join-Path $Root 'service'
$HomeDir = if ($env:QQ_AGENT_HOME) { $env:QQ_AGENT_HOME } else { Join-Path $env:USERPROFILE '.dsh\qq-agent' }
$env:QQ_AGENT_HOME = $HomeDir
New-Item -ItemType Directory -Force -Path (Join-Path $HomeDir 'logs') | Out-Null

$Py = Join-Path $Service '.venv\Scripts\python.exe'
if (-not (Test-Path $Py)) { $Py = 'python' }

& $Py -m qq_agent qq --listen --log-file (Join-Path $HomeDir 'logs\service.log')
exit $LASTEXITCODE
