param(
  [string]$BaseUrl = $env:HA_BASE_URL,
  [string]$Token = $env:HA_TOKEN,
  [double]$LowFlowGpm = 0.4,               # <= max low-flow 0.5
  [int]$LowFlowSeedS = 5,                  # seed per your settings
  [int]$LowFlowMinS = 30,                  # min duration per your settings
  [int]$LowFlowClearS = 5,                 # clear idle seconds
  [double]$TankFlowGpm = 2.5,              # per your instruction
  [int]$TankOnS = 30,                      # ~1.25 gal at 2.5 gpm
  [int]$TankOffS = 30,                     # allow gap tolerance to finalize
  [int]$TankRepeats = 3,                   # repeat count
  [int]$TestGapS = 60,                     # gap between test types to prevent chaining
  [string]$ValveEntityId = 'input_boolean.test_shutoff_valve',  # valve for auto-shutoff tests
  [switch]$RestartFirst,
  [string]$NumberEntityId = 'number.water_monitor_synth_synthetic_flow_gpm'
)

if (-not $BaseUrl) { $BaseUrl = "http://10.0.0.55:8123" }
if (-not $Token) { throw "HA_TOKEN env var not set" }

$hdrJson = @{ Authorization = "Bearer $Token"; 'Content-Type' = 'application/json' }
$hdrBasic = @{ Authorization = "Bearer $Token" }

function Restart-HA {
  Write-Host "Restarting Home Assistant..." -ForegroundColor Yellow
  try { Invoke-RestMethod -Method Post -Uri "$BaseUrl/api/services/homeassistant/restart" -Headers $hdrJson -Body '{}' | Out-Null } catch {}
  Start-Sleep -Seconds 10
  for ($i=0; $i -lt 60; $i++) { try { Invoke-RestMethod -Method Get -Uri "$BaseUrl/api/" -Headers $hdrBasic | Out-Null; break } catch { Start-Sleep -Seconds 1 } }
  Write-Host "Home Assistant is up." -ForegroundColor Yellow
}

function Set-Synth([double]$v) {
  $body = @{ entity_id = $NumberEntityId; value = $v } | ConvertTo-Json -Compress
  Invoke-RestMethod -Method Post -Uri "$BaseUrl/api/services/number/set_value" -Headers $hdrJson -Body $body | Out-Null
}

function Invoke-HAService([string]$domain, [string]$service, [hashtable]$data) {
  $body = $data | ConvertTo-Json -Compress
  Invoke-RestMethod -Method Post -Uri "$BaseUrl/api/services/$domain/$service" -Headers $hdrJson -Body $body | Out-Null
}

function Set-ValveOn([string]$valveId) {
  if (-not $valveId) { return }
  try {
    $domain = $valveId.Split('.')[0]
    switch ($domain) {
      'valve' { Invoke-HAService 'valve' 'open_valve' @{ entity_id = $valveId } }
      'input_boolean' { Invoke-HAService 'input_boolean' 'turn_on' @{ entity_id = $valveId } }
      'switch' { Invoke-HAService 'switch' 'turn_on' @{ entity_id = $valveId } }
      default { Write-Warning "Unknown valve domain: $domain" }
    }
    Write-Host "Valve turned ON: $valveId" -ForegroundColor Green
  } catch {
    Write-Warning "Failed to turn on valve $valveId : $_"
  }
}

function Find-EntityByName([string]$nameLike, [string]$fallbackLike) {
  $states = Invoke-RestMethod -Method Get -Uri "$BaseUrl/api/states" -Headers $hdrBasic
  $cand = $states | Where-Object { $_.attributes.friendly_name -like $nameLike }
  if (-not $cand -and $fallbackLike) { $cand = $states | Where-Object { $_.entity_id -like $fallbackLike } }
  if ($cand) { return ($cand[0].entity_id) }
  return $null
}

function Get-State([string]$entityId) {
  Invoke-RestMethod -Method Get -Uri "$BaseUrl/api/states/$entityId" -Headers $hdrBasic
}

function Wait-Condition([ScriptBlock]$cond, [int]$timeoutS = 120, [int]$intervalS = 1) {
  $deadline = (Get-Date).AddSeconds($timeoutS)
  while ((Get-Date) -lt $deadline) {
    if (& $cond) { return $true }
    Start-Sleep -Seconds $intervalS
  }
  return $false
}

if ($RestartFirst) { Restart-HA }

# Resolve binary sensor entity ids
$lowFlowId = Find-EntityByName -nameLike '*Low-flow leak*' -fallbackLike 'binary_sensor.*low*flow*leak*'
$tankLeakId = Find-EntityByName -nameLike '*Tank refill leak*' -fallbackLike 'binary_sensor.*tank*refill*leak*'
if (-not $lowFlowId -or -not $tankLeakId) {
  Write-Warning "Could not resolve binary sensors. lowFlowId=$lowFlowId tankLeakId=$tankLeakId"
}

# Try to detect valve entity from low-flow sensor attributes if not provided
$detectedValve = $null
if (-not $ValveEntityId -or $ValveEntityId -eq 'input_boolean.test_shutoff_valve') {
  try {
    $lowState = Get-State $lowFlowId
    $detectedValve = $lowState.attributes.auto_shutoff_valve_entity
    if ($detectedValve) {
      $ValveEntityId = $detectedValve
      Write-Host "Auto-detected valve: $ValveEntityId" -ForegroundColor Cyan
    }
  } catch {
    Write-Warning "Could not auto-detect valve entity"
  }
}

Write-Host "Using: lowFlow=$lowFlowId tankLeak=$tankLeakId valve=$ValveEntityId number=$NumberEntityId" -ForegroundColor Cyan

# Baseline zero and ensure valve is ON
Set-Synth 0.0; Start-Sleep -Seconds 2
Set-ValveOn $ValveEntityId

# ---- Low-flow leak test ----
Write-Host "Low-flow: setting $LowFlowGpm gpm for $(($LowFlowSeedS+$LowFlowMinS+3))s" -ForegroundColor Cyan
Set-Synth $LowFlowGpm
$lowOn = Wait-Condition -timeoutS ($LowFlowSeedS + $LowFlowMinS + 30) -intervalS 1 -cond {
  try {
    $st = Get-State $lowFlowId
    return ($st.state -eq 'on')
  } catch { return $false }
}
Set-Synth 0.0
$cleared = Wait-Condition -timeoutS ($LowFlowClearS + 20) -intervalS 1 -cond {
  try { $st = Get-State $lowFlowId; return ($st.state -eq 'off') } catch { return $false }
}
$lowSt = Get-State $lowFlowId
Write-Host ("Low-flow: on={0} phase={1} seed={2}s count={3}s idle_zero={4}s" -f $lowOn, $lowSt.attributes.phase, $lowSt.attributes.seed_progress_s, $lowSt.attributes.count_progress_s, $lowSt.attributes.idle_zero_s)
Write-Host ("Low-flow cleared={0}" -f $cleared)

# Gap between tests to prevent chaining + turn valve back ON
Write-Host "Waiting $TestGapS seconds between tests to prevent chaining..." -ForegroundColor Yellow
Set-Synth 0.0
Set-ValveOn $ValveEntityId
Start-Sleep -Seconds $TestGapS

# ---- Tank refill leak test ----
Write-Host "Tank leak: $TankRepeats refills at $TankFlowGpm gpm, $TankOnS s ON / $TankOffS s OFF" -ForegroundColor Cyan
for ($i=1; $i -le $TankRepeats; $i++) {
  Set-Synth $TankFlowGpm; Start-Sleep -Seconds $TankOnS; Set-Synth 0.0; Start-Sleep -Seconds $TankOffS
}
$tlOn = Wait-Condition -timeoutS 120 -intervalS 2 -cond {
  try { $st = Get-State $tankLeakId; return ($st.state -eq 'on') } catch { return $false }
}
$tl = Get-State $tankLeakId
Write-Host ("Tank leak: on={0} similar_count={1} events_in_window={2} last_event={3}" -f $tlOn, $tl.attributes.similar_count, $tl.attributes.events_in_window, $tl.attributes.last_event)

# Final reset and restore valve
Write-Host "Leak validation tests completed." -ForegroundColor Green
