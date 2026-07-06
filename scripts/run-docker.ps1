param(
  [switch]$Rebuild,
  [switch]$Detached,
  [switch]$Down,
  [switch]$Logs,
  [string]$Service = "",
  [string]$WslDistro = "",
  [string]$LinuxRepoPath = ""
)

$ErrorActionPreference = "Stop"

function Get-WslRepoInfo {
  param(
    [string]$WindowsPath,
    [string]$PreferredDistro,
    [string]$PreferredLinuxRepoPath
  )

  if ($PreferredLinuxRepoPath) {
    return @{
      Distro = $(if ($PreferredDistro) { $PreferredDistro } else { "Ubuntu" })
      LinuxPath = $PreferredLinuxRepoPath
    }
  }

  if ($WindowsPath -match '^\\\\wsl(?:\.localhost|\$)\\([^\\]+)\\(.+)$') {
    return @{
      Distro = $(if ($PreferredDistro) { $PreferredDistro } else { $Matches[1] })
      LinuxPath = "/" + (($Matches[2] -replace '\\', '/').TrimStart('/'))
    }
  }

  return $null
}

function Invoke-DockerCompose {
  param(
    [string[]]$ComposeArgs,
    [hashtable]$WslRepo
  )

  if ($WslRepo) {
    $wslArgs = @(
      "-d", $WslRepo.Distro,
      "--cd", $WslRepo.LinuxPath,
      "--exec", "docker"
    ) + $ComposeArgs

    & wsl.exe @wslArgs
    if ($LASTEXITCODE -ne 0) {
      throw "WSL docker command failed. If WSL is down, run 'wsl --shutdown' and try again."
    }
    return
  }

  docker @ComposeArgs
  if ($LASTEXITCODE -ne 0) {
    throw "docker command failed"
  }
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$wslRepo = Get-WslRepoInfo -WindowsPath $repoRoot -PreferredDistro $WslDistro -PreferredLinuxRepoPath $LinuxRepoPath

if (-not $wslRepo) {
  Set-Location $repoRoot
}

$composeArgs = @("compose")

if ($Logs) {
  $composeArgs += @("logs", "-f")
  if ($Service) {
    $composeArgs += $Service
  }
}
elseif ($Down) {
  $composeArgs += "down"
}
else {
  $composeArgs += "up"
  if ($Rebuild) {
    $composeArgs += "--build"
  }
  if ($Detached) {
    $composeArgs += "-d"
  }
  if ($Service) {
    $composeArgs += $Service
  }
}

Invoke-DockerCompose -ComposeArgs $composeArgs -WslRepo $wslRepo
