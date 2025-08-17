param(
  [string]$Host = "homeassistant.local",
  [int]$Port = 22,
  [string]$User = "root",
  [string]$ConfigPath = "/config",
  [switch]$NoRestart
)

$ErrorActionPreference = 'Stop'

function Require-Cli($name) {
  if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
    throw "Required CLI '$name' not found in PATH. Please install OpenSSH client."
  }
}

try {
  Require-Cli ssh
  Require-Cli scp

  $repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
  $componentLocalPath = Join-Path $repoRoot "custom_components/water_monitor"
  if (-not (Test-Path $componentLocalPath)) {
    throw "Local component path not found: $componentLocalPath"
  }

  $remotePath = "$ConfigPath/custom_components/water_monitor"
  Write-Host "Deploying to $User@$Host:$remotePath" -ForegroundColor Cyan

  # Ensure remote directory exists and clear old files
  $prepCmd = "mkdir -p '$remotePath' && rm -rf '$remotePath'/*"
  & ssh -p $Port "$User@$Host" $prepCmd

  # Copy files recursively with compression
  & scp -P $Port -r -C "${componentLocalPath}/*" "$User@$Host:$remotePath/"

  if (-not $NoRestart) {
    Write-Host "Restarting Home Assistant core..." -ForegroundColor Cyan
    $restartCmd = @(
      "ha core restart",
      # Fallbacks commonly seen outside HA OS
      "docker restart homeassistant",
      "systemctl restart home-assistant@homeassistant.service"
    ) -join " || "
    & ssh -p $Port "$User@$Host" $restartCmd
    Write-Host "Restart command sent." -ForegroundColor Green
  } else {
    Write-Host "Skipping restart as requested (use -NoRestart to keep running)." -ForegroundColor Yellow
  }

  Write-Host "Deployment complete." -ForegroundColor Green
} catch {
  Write-Error $_
  exit 1
}
