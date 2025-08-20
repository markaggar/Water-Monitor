param(
  [string]$BaseUrl = $env:HA_BASE_URL,
  [string]$Token = $env:HA_TOKEN,
  [string]$ValveEntityId = 'input_boolean.test_shutoff_valve',
  [string]$LowFlowEntityId = 'binary_sensor.water_monitor_synth_low_flow_leak',
  [string]$LowFlowSensorLike = '*Low-flow leak*',
  [double]$LowFlowGpm = 0.4,
  [string]$NumberEntityId = 'number.water_monitor_synth_synthetic_flow_gpm',
  [int]$StayOnMinutes = 10
)

if (-not $BaseUrl) { $BaseUrl = 'http://10.0.0.55:8123' }
if (-not $Token) { throw 'HA_TOKEN env var not set' }
$hdrJson = @{ Authorization = "Bearer $Token"; 'Content-Type' = 'application/json' }
$hdrBasic = @{ Authorization = "Bearer $Token" }

function Get-State([string]$entityId) {
  Invoke-RestMethod -Method Get -Uri "$BaseUrl/api/states/$entityId" -Headers $hdrBasic -TimeoutSec 15
}
function Invoke-HAService([string]$domain, [string]$service, [hashtable]$data) {
  $body = $data | ConvertTo-Json -Compress
  Invoke-RestMethod -Method Post -Uri "$BaseUrl/api/services/$domain/$service" -Headers $hdrJson -Body $body -TimeoutSec 20 | Out-Null
}
function Get-EntityByName([string]$nameLike, [string]$fallbackLike) {
  $states = Invoke-RestMethod -Method Get -Uri "$BaseUrl/api/states" -Headers $hdrBasic -TimeoutSec 30
  $cand = $states | Where-Object { $_.attributes.friendly_name -like $nameLike }
  if (-not $cand -and $fallbackLike) { $cand = $states | Where-Object { $_.entity_id -like $fallbackLike } }
  if ($cand) { return ($cand[0].entity_id) }
  return $null
}
function Wait-Until([scriptblock]$cond, [int]$timeoutS = 120, [int]$intervalS = 1) {
  $deadline = (Get-Date).AddSeconds($timeoutS)
  while ((Get-Date) -lt $deadline) {
    if (& $cond) { return $true }
    Start-Sleep -Seconds $intervalS
  }
  return $false
}

Write-Host "Starting low-flow auto-shutoff validation..." -ForegroundColor Cyan

# Resolve low-flow leak sensor id (prefer explicit)
$lowFlowId = $null
if ($LowFlowEntityId) {
  try { Get-State $LowFlowEntityId | Out-Null; $lowFlowId = $LowFlowEntityId } catch { }
}
if (-not $lowFlowId) {
  $lowFlowId = Get-EntityByName -nameLike $LowFlowSensorLike -fallbackLike 'binary_sensor.*low*flow*leak*'
}
if (-not $lowFlowId) { Write-Host 'Low-flow leak sensor not found' -ForegroundColor Red; exit 2 }

# Confirm valve entity exists
try { Get-State $ValveEntityId | Out-Null } catch { Write-Host "Valve entity not found: $ValveEntityId" -ForegroundColor Red; exit 2 }

# Ensure valve is ON to start
try { Invoke-HAService 'input_boolean' 'turn_on' @{ entity_id = $ValveEntityId } } catch {}

# Reset synthetic flow to 0
try { Invoke-HAService 'number' 'set_value' @{ entity_id = $NumberEntityId; value = 0 } } catch {}
Start-Sleep -Seconds 2

# Read low-flow attributes to compute hold time and verify auto-shutoff is enabled
$seedS = 60; $minS = 300; $clearIdleS = 30
try {
  $lf = Get-State $lowFlowId
  $autoEff = $lf.attributes.auto_shutoff_effective
  $autoOn = $lf.attributes.auto_shutoff_on_trigger
  $autoValve = $lf.attributes.auto_shutoff_valve_entity
  if (-not $autoEff) {
    Write-Host 'Auto-shutoff is not enabled/effective for low-flow. Enable the per-detector toggle and ensure a shutoff valve is configured.' -ForegroundColor Red
    exit 3
  }
  if ($autoValve -ne $ValveEntityId) {
    Write-Host ("Configured shutoff valve for low-flow is '{0}', which does not match expected '{1}'." -f $autoValve, $ValveEntityId) -ForegroundColor Red
    exit 3
  }
  if ($lf.attributes.seed_required_s -is [int] -or $lf.attributes.seed_required_s -is [double]) { $seedS = [int]$lf.attributes.seed_required_s }
  if ($lf.attributes.min_duration_s -is [int] -or $lf.attributes.min_duration_s -is [double]) { $minS = [int]$lf.attributes.min_duration_s }
  if ($lf.attributes.clear_idle_s -is [int] -or $lf.attributes.clear_idle_s -is [double]) { $clearIdleS = [int]$lf.attributes.clear_idle_s }
} catch {}
$holdS = [int]($seedS + $minS + 20)
Write-Host ("Using thresholds: seed={0}s min={1}s clear_idle={2}s; holding low-flow for ~{3}s" -f $seedS,$minS,$clearIdleS,$holdS) -ForegroundColor DarkGray

# Start low-flow
Write-Host ("Setting synthetic flow to {0} gpm" -f $LowFlowGpm) -ForegroundColor Cyan
Invoke-HAService 'number' 'set_value' @{ entity_id = $NumberEntityId; value = $LowFlowGpm }

# Wait for low-flow sensor to turn on
$on = Wait-Until -timeoutS ($holdS + 90) -intervalS 1 -cond {
  try { (Get-State $lowFlowId).state -eq 'on' } catch { $false }
}
if (-not $on) { Write-Host 'Low-flow did not turn ON in time.' -ForegroundColor Red; Invoke-HAService 'number' 'set_value' @{ entity_id = $NumberEntityId; value = 0 }; exit 2 }
$onTs = Get-Date
Write-Host ("Low-flow is ON at {0}" -f $onTs) -ForegroundColor Green

# Validate auto-shutoff: valve turns OFF shortly after trigger
$valveOff = Wait-Until -timeoutS 20 -intervalS 1 -cond {
  try { (Get-State $ValveEntityId).state -eq 'off' } catch { $false }
}
if (-not $valveOff) {
  Write-Host "Valve did not turn OFF automatically." -ForegroundColor Red
  Invoke-HAService 'number' 'set_value' @{ entity_id = $NumberEntityId; value = 0 }
  exit 2
}
Write-Host "Valve is OFF (auto-shutoff confirmed)." -ForegroundColor Green

# Ensure low-flow stays ON for at least the requested minutes while valve is OFF
$stayS = [int](60 * $StayOnMinutes)
$endTs = $onTs.AddSeconds($stayS)
while ((Get-Date) -lt $endTs) {
  try {
    $s = Get-State $lowFlowId
  if ($s.state -ne 'on') { Write-Host ("Low-flow turned OFF early at {0}" -f (Get-Date)) -ForegroundColor Red; Invoke-HAService 'number' 'set_value' @{ entity_id = $NumberEntityId; value = 0 }; exit 2 }
  } catch {}
  # progress heartbeat every minute
  Start-Sleep -Seconds 30
}
Write-Host ("Low-flow remained ON for at least {0} minutes." -f $StayOnMinutes) -ForegroundColor Green

# Turn valve back ON and expect the leak to clear after idle
Write-Host 'Turning valve back ON...' -ForegroundColor Cyan
Invoke-HAService 'input_boolean' 'turn_on' @{ entity_id = $ValveEntityId }

# Ensure synthetic flow is 0 so idle timer can clear
Invoke-HAService 'number' 'set_value' @{ entity_id = $NumberEntityId; value = 0 }

# Wait for clear (clear_idle_s + buffer)
$cleared = Wait-Until -timeoutS ([int]($clearIdleS + 120)) -intervalS 2 -cond {
  try { (Get-State $lowFlowId).state -eq 'off' } catch { $false }
}
if (-not $cleared) { Write-Host 'Low-flow did not clear after valve ON and idle.' -ForegroundColor Red; exit 2 }
$offTs = Get-Date
Write-Host ("Low-flow cleared at {0}" -f $offTs) -ForegroundColor Green

Write-Host 'Validation PASS.' -ForegroundColor Green
exit 0
