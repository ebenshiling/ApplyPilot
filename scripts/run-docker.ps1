param(
  [switch]$Rebuild,
  [switch]$Detached,
  [switch]$Down
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repoRoot

if ($Down) {
  docker compose down
  exit 0
}

$args = @("compose", "up")
if ($Rebuild) {
  $args += "--build"
}
if ($Detached) {
  $args += "-d"
}

docker @args
