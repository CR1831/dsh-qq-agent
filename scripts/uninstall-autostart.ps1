#requires -Version 5.1
$ErrorActionPreference = 'Stop'
$Startup = [Environment]::GetFolderPath('Startup')
$Link = Join-Path $Startup 'qq-agent.lnk'
if (Test-Path $Link) {
    Remove-Item $Link -Force
    Write-Host "Removed: $Link"
} else {
    Write-Host "Nothing to remove: $Link"
}
