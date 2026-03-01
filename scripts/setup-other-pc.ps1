param(
  [string]$PythonPath = "",
  [string]$VenvPath = ".venv",
  [string]$TransferZip = "",
  [string]$ApplyPilotHome = "",
  [switch]$SkipJobSpy,
  [switch]$WithPlaywright,
  [switch]$StartDashboard,
  [switch]$MultiUser
)

$ErrorActionPreference = "Stop"

function Write-Step {
  param([string]$Message)
  Write-Host "`n[setup] $Message" -ForegroundColor Cyan
}

function Invoke-Tokens {
  param(
    [string[]]$Tokens,
    [string[]]$Args
  )
  $exe = $Tokens[0]
  $prefix = @()
  if ($Tokens.Length -gt 1) {
    $prefix = $Tokens[1..($Tokens.Length - 1)]
  }
  & $exe @prefix @Args
  if ($LASTEXITCODE -ne 0) {
    throw "Command failed: $exe $($prefix -join ' ') $($Args -join ' ')"
  }
}

function Resolve-BootstrapPython {
  param([string]$PreferredPath)

  $candidates = @()

  if ($PreferredPath) {
    $expanded = [Environment]::ExpandEnvironmentVariables($PreferredPath)
    if (-not (Test-Path $expanded)) {
      throw "PythonPath does not exist: $expanded"
    }
    $candidates += ,@($expanded)
  }

  if (Get-Command py -ErrorAction SilentlyContinue) {
    $candidates += ,@("py", "-3.11")
    $candidates += ,@("py", "-3")
    $candidates += ,@("py")
  }

  if (Get-Command python -ErrorAction SilentlyContinue) {
    $candidates += ,@("python")
  }

  foreach ($cand in $candidates) {
    try {
      Invoke-Tokens -Tokens $cand -Args @("-c", "import sys;print(sys.executable)")
      return $cand
    }
    catch {
      continue
    }
  }

  throw "No usable Python interpreter found. Install Python 3.10+ or pass -PythonPath <path-to-python.exe>."
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repoRoot

if ($ApplyPilotHome) {
  $homePath = [Environment]::ExpandEnvironmentVariables($ApplyPilotHome)
  $env:APPLYPILOT_HOME = $homePath
  Write-Step "Using APPLYPILOT_HOME=$homePath"
}

$venvRoot = [IO.Path]::GetFullPath((Join-Path $repoRoot $VenvPath))
$venvPython = Join-Path $venvRoot "Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
  Write-Step "Creating virtual environment at $venvRoot"
  $bootstrapPython = Resolve-BootstrapPython -PreferredPath $PythonPath
  Invoke-Tokens -Tokens $bootstrapPython -Args @("-m", "venv", $venvRoot)
}
else {
  Write-Step "Virtual environment already exists: $venvRoot"
}

if (-not (Test-Path $venvPython)) {
  throw "Virtual environment python not found at $venvPython"
}

Write-Step "Upgrading pip"
& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed" }

Write-Step "Installing ApplyPilot in editable mode"
& $venvPython -m pip install -e .
if ($LASTEXITCODE -ne 0) { throw "pip install -e . failed" }

if (-not $SkipJobSpy) {
  Write-Step "Installing JobSpy compatibility packages"
  & $venvPython -m pip install --no-deps python-jobspy
  if ($LASTEXITCODE -ne 0) { throw "python-jobspy install failed" }
  & $venvPython -m pip install pydantic tls-client requests markdownify regex
  if ($LASTEXITCODE -ne 0) { throw "JobSpy dependency install failed" }
}
else {
  Write-Step "Skipping JobSpy compatibility packages (-SkipJobSpy)"
}

if ($WithPlaywright) {
  Write-Step "Installing Playwright Chromium browser"
  & $venvPython -m playwright install chromium
  if ($LASTEXITCODE -ne 0) { throw "Playwright browser install failed" }
}

if ($TransferZip) {
  $zipPath = [Environment]::ExpandEnvironmentVariables($TransferZip)
  if (-not (Test-Path $zipPath)) {
    throw "Transfer zip not found: $zipPath"
  }
  Write-Step "Importing workspace archive: $zipPath"
  & $venvPython -m applypilot workspace-import $zipPath --overwrite
  if ($LASTEXITCODE -ne 0) { throw "workspace-import failed" }
}

Write-Step "Setup complete"
Write-Host "[setup] Run dashboard with:" -ForegroundColor Green
Write-Host "[setup]   $venvPython -m applypilot dashboard-serve --multi-user" -ForegroundColor Green

if ($StartDashboard) {
  $dashArgs = @("-m", "applypilot", "dashboard-serve")
  if ($MultiUser) {
    $dashArgs += "--multi-user"
  }
  Write-Step "Starting dashboard"
  & $venvPython @dashArgs
}
