param(
  [string]$Distro = "Ubuntu",
  [switch]$SkipServiceRestart,
  [switch]$CheckDocker
)

$ErrorActionPreference = "Stop"

function Write-Step {
  param([string]$Message)
  Write-Host "`n[fix-wsl] $Message" -ForegroundColor Cyan
}

function Invoke-Step {
  param(
    [scriptblock]$Action,
    [string]$ErrorMessage
  )

  try {
    & $Action
  }
  catch {
    throw "$ErrorMessage`n$($_.Exception.Message)"
  }
}

Write-Step "Switching to home directory"
Set-Location $HOME

Write-Step "Shutting down WSL"
Invoke-Step -Action { wsl --shutdown } -ErrorMessage "WSL shutdown failed."

if (-not $SkipServiceRestart) {
  Write-Step "Restarting WSL-related Windows services"
  Invoke-Step -Action {
    Get-Service LxssManager, vmcompute | Restart-Service
  } -ErrorMessage "Service restart failed. Try running this script as Administrator."
}

Write-Step "Listing WSL distros"
Invoke-Step -Action { wsl -l -v } -ErrorMessage "Failed to list WSL distros."

Write-Step "Testing distro '$Distro'"
Invoke-Step -Action { wsl -d $Distro -- uname -a } -ErrorMessage "Failed to start WSL distro '$Distro'."

if ($CheckDocker) {
  Write-Step "Checking Docker inside WSL"
  Invoke-Step -Action { wsl -d $Distro -- docker ps } -ErrorMessage "Docker check inside WSL failed."
}

Write-Host "`n[fix-wsl] WSL is responding again." -ForegroundColor Green
Write-Host "[fix-wsl] You can now run docker commands, for example:" -ForegroundColor Green
Write-Host "[fix-wsl]   .\scripts\run-docker.ps1 -Logs -Service applypilot" -ForegroundColor Green
