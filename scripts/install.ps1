#requires -Version 5.1
<#
Create the Python environment and the runtime home for dsh-qq-agent.

This script never creates a persona card.  It copies the neutral template to
<home>\persona.example.md and tells the user where to put their own card.
#>
[CmdletBinding()]
param(
    [switch]$SkipDeps
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Service = Join-Path $Root 'service'
$HomeDir = if ($env:QQ_AGENT_HOME) { $env:QQ_AGENT_HOME } else { Join-Path $env:USERPROFILE '.dsh\qq-agent' }

if (-not (Test-Path $Service)) { throw "service directory not found: $Service" }
New-Item -ItemType Directory -Force -Path $HomeDir | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $HomeDir 'data\qq') | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $HomeDir 'logs') | Out-Null

$Venv = Join-Path $Service '.venv'
$Py = Join-Path $Venv 'Scripts\python.exe'
if (-not (Test-Path $Py)) {
    Write-Host "Creating venv: $Venv"
    $base = Get-Command python -ErrorAction SilentlyContinue
    if (-not $base) { throw 'python not found on PATH. Install Python 3.11+ first.' }
    & $base.Source -m venv $Venv
}

if (-not $SkipDeps) {
    Write-Host 'Installing the QQ agent (editable) ...'
    & $Py -m pip install --upgrade pip
    & $Py -m pip install -e $Service
}

$Config = Join-Path $HomeDir 'config.toml'
if (-not (Test-Path $Config)) {
    Copy-Item (Join-Path $Service 'config.example.toml') $Config
    Write-Host "Created config: $Config"
} else {
    Write-Host "Config exists: $Config"
}

$Template = Join-Path $HomeDir 'persona.example.md'
Copy-Item (Join-Path $Root 'persona.example.md') $Template -Force
Write-Host "Persona template: $Template"

Write-Host ''
Write-Host 'Next steps:' -ForegroundColor Cyan
Write-Host "  1. Edit $Config"
Write-Host '     - [qq] enabled = true'
Write-Host '     - self_id / owner_uid / friend_whitelist'
Write-Host '     - api_base / ws_url / access_token (from NapCat)'
Write-Host '     - [agent] api_key, or set QQ_AGENT_AGENT__API_KEY'
Write-Host "  2. Put your own card at $HomeDir\persona.md"
Write-Host '     Markdown, SillyTavern JSON, or SillyTavern PNG are accepted.'
Write-Host '     Do not commit it; it stays outside the repository.'
Write-Host "  3. Run: $Root\scripts\run-qq.ps1"
Write-Host "  4. Optional: $Root\scripts\install-autostart.ps1"
Write-Host "  5. DSH: install the bundle at $Root (plugin manager install_bundle)"
