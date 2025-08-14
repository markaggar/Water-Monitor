param(
    [string]$DestPath = "\\10.0.0.55\config\custom_components\water_monitor"
)

# Resolve repo root relative to this script
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$src = Join-Path $repoRoot "custom_components\water_monitor\"

Write-Host "Deploying Water Monitor from: $src" -ForegroundColor Cyan
Write-Host "To: $DestPath" -ForegroundColor Cyan

if (-not (Test-Path $src)) {
    Write-Warning "Source path not found: $src"
    exit 0
}
if (-not (Test-Path $DestPath)) {
    Write-Host "Destination missing; creating: $DestPath"
    New-Item -ItemType Directory -Force -Path $DestPath | Out-Null
}

# Copy files, exclude caches/pyc; /FFT for FAT time granularity on Samba; /IS include same (force overwrite)
$robocopyArgs = @(
    '"{0}"' -f $src,
    '"{0}"' -f $DestPath,
    '*.*','/E','/R:2','/W:2','/FFT','/IS',
    '/XF','*.pyc','*.pyo',
    '/XD','__pycache__'
)

# Run and capture exit code; treat 0..7 as success per Robocopy semantics
& robocopy $robocopyArgs | Out-Null
$code = $LASTEXITCODE
if ($code -lt 0) { $code = 16 }
if ($code -le 7) {
    Write-Host "Robocopy OK (code $code)" -ForegroundColor DarkGray
    exit 0
} else {
    Write-Warning "Robocopy reported error (code $code)"
    exit 0
}
