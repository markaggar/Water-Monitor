param(
    [string]$DestPath = "\\10.0.0.55\config\custom_components\water_monitor",
    [string]$PackagesDest = "\\10.0.0.55\config\packages",
    [string]$VerifyEntity,
    [switch]$DumpErrorLog,
    [switch]$DumpErrorLogOnFail
)

# Resolve repo root relative to this script
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$src = Join-Path $repoRoot "custom_components\water_monitor"

# Track verification failure to signal regressions via exit code
$script:wmFail = $false
$componentCopied = $false
$packagesCopied = $false

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
    $src,
    $DestPath,
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
}

# Mark component changes if any files were copied (bit 0x01)
if ( ($code -band 1) -ne 0 ) { $componentCopied = $true }

if ($code -gt 7) {
    # Fallback: Copy-Item per file if Robocopy failed
    Write-Warning "Robocopy reported error (code $code); attempting fallback copy..."
    try {
        $files = Get-ChildItem -Path $src -Recurse -File -Force -ErrorAction Stop |
            Where-Object { $_.Name -notmatch '\\.pyc$|\\.pyo$' -and $_.FullName -notmatch '\\__pycache__\\' }
        foreach ($f in $files) {
            $rel = $f.FullName.Substring($src.Length).TrimStart([char[]]"/\")
            $destFile = Join-Path $DestPath $rel
            $destDir = Split-Path -Parent $destFile
            if (-not (Test-Path $destDir)) { New-Item -ItemType Directory -Force -Path $destDir | Out-Null }
            Copy-Item -Path $f.FullName -Destination $destFile -Force
        }
        Write-Host "Fallback copy completed" -ForegroundColor Yellow
    } catch {
        Write-Warning "Fallback copy failed: $($_.Exception.Message)"
    }
}

# Also deploy any YAML packages (file name contains 'package' and ends with .yaml) to HA packages folder
try {
    if (-not (Test-Path $PackagesDest)) {
        Write-Host "Packages destination missing; creating: $PackagesDest" -ForegroundColor DarkGray
        New-Item -ItemType Directory -Force -Path $PackagesDest | Out-Null
    }
    $pkgFiles = Get-ChildItem -Path $repoRoot -Recurse -File -Include "*package*.yaml" -ErrorAction SilentlyContinue
    foreach ($pf in $pkgFiles) {
        # Use robocopy to only copy if file changed; flatten into the packages root
        $srcDir = Split-Path -Parent $pf.FullName
        $mask = Split-Path -Leaf $pf.FullName
        $pkgArgs = @(
            $srcDir,
            $PackagesDest,
            $mask,'/R:2','/W:2','/FFT','/IS'
        )
        & robocopy $pkgArgs | Out-Null
        $pcode = $LASTEXITCODE
        if ($pcode -lt 0) { $pcode = 16 }
        if ($pcode -le 7) {
            if ( ($pcode -band 1) -ne 0 ) {
                $packagesCopied = $true
                Write-Host "Copied package: $mask -> $PackagesDest (code $pcode)" -ForegroundColor DarkGray
            }
        } else {
            Write-Warning "Robocopy failed for package $mask (code $pcode)"
        }
    }
} catch {
    Write-Warning "Package copy step error: $($_.Exception.Message)"
}

# If no changes to component or packages, skip HA restart entirely
if (-not $componentCopied -and -not $packagesCopied) {
    Write-Host "No changes detected (component or packages). Skipping HA restart." -ForegroundColor DarkGray
    exit 0
}

# Optional: Restart Home Assistant via REST if env vars are present
$baseUrl = $env:HA_BASE_URL
$token = $env:HA_TOKEN
if ([string]::IsNullOrWhiteSpace($baseUrl)) {
    if ($DestPath -match '^\\\\([^\\]+)\\') {
    $haHost = $Matches[1]
    $baseUrl = "http://$haHost:8123"
        Write-Host "HA_BASE_URL not set; inferring $baseUrl from share host" -ForegroundColor DarkGray
    }
}

if (-not [string]::IsNullOrWhiteSpace($baseUrl) -and -not [string]::IsNullOrWhiteSpace($token)) {
    $headers = @{ Authorization = "Bearer $token"; 'Content-Type' = 'application/json' }
    
    function Fetch-HAErrorLog {
        param([string]$why)
        try {
            $logUri = "$baseUrl/api/error_log"
            $logHeaders = @{ Authorization = "Bearer $token"; 'Accept' = 'text/plain' }
            Write-Host "Fetching HA error log ($why): $logUri" -ForegroundColor DarkGray
            $logResp = Invoke-WebRequest -Method Get -Uri $logUri -Headers $logHeaders -TimeoutSec 20 -ErrorAction Stop
            $content = $logResp.Content
            if (-not [string]::IsNullOrEmpty($content)) {
                $ts = Get-Date -Format 'yyyyMMdd-HHmmss'
                $outFile = Join-Path $repoRoot "ha-error-log-$ts.log"
                $content | Out-File -FilePath $outFile -Encoding UTF8
                Write-Host "Saved HA error log to $outFile" -ForegroundColor DarkGray
                # Print a short tail to console for quick visibility
                $lines = $content -split "`n"
                $tail = ($lines | Select-Object -Last 80) -join "`n"
                if ($tail) { Write-Host "--- HA error log (last 80 lines) ---`n$tail`n--- end ---" -ForegroundColor DarkYellow }
            } else {
                Write-Host "HA error log returned empty content." -ForegroundColor DarkGray
            }
        } catch {
            Write-Warning "Failed to fetch HA error log: $($_.Exception.Message)"
        }
    }
    # Determine which entity to verify after restart
    if ([string]::IsNullOrWhiteSpace($VerifyEntity)) {
        if (-not [string]::IsNullOrWhiteSpace($env:HA_VERIFY_ENTITY)) {
            $VerifyEntity = $env:HA_VERIFY_ENTITY
        } else {
            $VerifyEntity = 'sensor.water_monitor_synth_last_session_volume'
        }
    }
    try {
        # Quick auth check
        $cfgUri = "$baseUrl/api/config"
        Write-Host "Checking HA API: $cfgUri" -ForegroundColor DarkGray
        $cfgResp = Invoke-WebRequest -Method Get -Uri $cfgUri -Headers $headers -TimeoutSec 15 -ErrorAction Stop
        Write-Host "HA API reachable (HTTP $($cfgResp.StatusCode))." -ForegroundColor DarkGray
    } catch {
        Write-Warning "HA API check failed: $($_.Exception.Message)"
    }
    $uri = "$baseUrl/api/services/homeassistant/restart"
    try {
        Write-Host "Requesting HA Core restart via REST: $uri" -ForegroundColor Cyan
        $resp = Invoke-WebRequest -Method Post -Uri $uri -Headers $headers -Body '{}' -TimeoutSec 30 -ErrorAction Stop
        Write-Host "HA restart requested (HTTP $($resp.StatusCode))." -ForegroundColor Green
    } catch {
        $msg = $_.Exception.Message
        $status = $null
        if ($_.Exception.Response) { try { $status = $_.Exception.Response.StatusCode.value__ } catch {} }
        Write-Warning "HA restart REST call failed: $msg (HTTP $status)"
        # Treat 502/504 or connection refused as expected during restart
        if ($status -in 502,504 -or $msg -match 'actively refused') {
            Write-Host "Restart likely in progress; HA may be temporarily unavailable." -ForegroundColor Yellow
        } else {
            # Retry once after 2s for transient issues
            Start-Sleep -Seconds 2
            try {
                $resp2 = Invoke-WebRequest -Method Post -Uri $uri -Headers $headers -Body '{}' -TimeoutSec 30 -ErrorAction Stop
                Write-Host "HA restart requested on retry (HTTP $($resp2.StatusCode))." -ForegroundColor Green
            } catch {
                Write-Warning "HA restart retry failed: $($_.Exception.Message)"
            }
        }
    }

    # Poll for HA to come back online
    $maxWait = 60
    if ($env:HA_RESTART_MAX_WAIT_SEC -match '^[0-9]+$') { $maxWait = [int]$env:HA_RESTART_MAX_WAIT_SEC }
    $interval = 2
    if ($env:HA_RESTART_POLL_INTERVAL_SEC -match '^[0-9]+$') { $interval = [int]$env:HA_RESTART_POLL_INTERVAL_SEC }
    $elapsed = 0
    $back = $false
    Write-Host "Waiting up to $maxWait s for HA to come back online..." -ForegroundColor DarkGray
    while ($elapsed -lt $maxWait) {
        try {
            $ping = Invoke-WebRequest -Method Get -Uri "$baseUrl/api/config" -Headers $headers -TimeoutSec 10 -ErrorAction Stop
            if ($ping.StatusCode -eq 200) {
                Write-Host "HA back online after ${elapsed}s (HTTP 200)." -ForegroundColor Green
                $back = $true
                break
            }
        } catch {
            # ignore until timeout
        }
        Start-Sleep -Seconds $interval
        $elapsed += $interval
    }
    if (-not $back) {
        Write-Warning "HA did not respond with HTTP 200 within $maxWait s; it may still be restarting."
    if ($DumpErrorLogOnFail) { Fetch-HAErrorLog -why 'restart did not return 200 in time' }
    }

    # Optional: verify a specific entity becomes available
    if ($back -and -not [string]::IsNullOrWhiteSpace($VerifyEntity)) {
        $verifyMaxWait = 45
        if ($env:HA_VERIFY_MAX_WAIT_SEC -match '^[0-9]+$') { $verifyMaxWait = [int]$env:HA_VERIFY_MAX_WAIT_SEC }
        $verifyInterval = 2
        if ($env:HA_VERIFY_POLL_INTERVAL_SEC -match '^[0-9]+$') { $verifyInterval = [int]$env:HA_VERIFY_POLL_INTERVAL_SEC }

        Write-Host "Verifying entity availability: $VerifyEntity (timeout ${verifyMaxWait}s)" -ForegroundColor DarkGray
        $ok = $false
        $elapsed = 0
        while ($elapsed -lt $verifyMaxWait) {
            try {
                $stateResp = Invoke-WebRequest -Method Get -Uri "$baseUrl/api/states/$VerifyEntity" -Headers $headers -TimeoutSec 10 -ErrorAction Stop
                if ($stateResp.StatusCode -eq 200) {
                    $obj = $stateResp.Content | ConvertFrom-Json
                    $st = [string]$obj.state
                    if ($st -and $st -ne 'unknown' -and $st -ne 'unavailable') {
                        Write-Host "Entity $VerifyEntity is available with state: $st" -ForegroundColor Green
                        $ok = $true
                        break
                    }
                }
            } catch {
                # 404 or other while HA is still initializing; keep waiting
            }
            Start-Sleep -Seconds $verifyInterval
            $elapsed += $verifyInterval
        }
        if (-not $ok) {
            Write-Warning "Entity $VerifyEntity was not available within ${verifyMaxWait}s. Possible regression introduced."
            $script:wmFail = $true
        if ($DumpErrorLogOnFail) { Fetch-HAErrorLog -why 'entity verification failed' }
        }
    }

    # Optional: always dump error log after restart (useful when diagnosing load issues)
    if ($DumpErrorLog) { Fetch-HAErrorLog -why 'post-restart (requested)'}
} else {
    Write-Host "Skipping HA restart (set HA_BASE_URL and HA_TOKEN to enable)." -ForegroundColor DarkGray
}

if ($script:wmFail) {
    # Non-zero exit to indicate verification failure
    exit 2
}
exit 0
