param(
  [string]$BaseUrl = $env:HA_BASE_URL, # e.g. http://homeassistant.local:8123
  [string]$Token = $env:HA_TOKEN,
  [double]$FlowGpm = 1.0,
  [int]$SteadySeconds = 10,
  [int]$Cycles = 2,
  [int]$OnSeconds = 10,
  [int]$OffSeconds = 5,
  [double]$TolVol = 0.05,
  [double]$TolAvg = 0.15,
  # Varying-flow test parameters
  [double]$VarFlow1 = 1.0,
  [int]$VarSec1 = 30,
  [double]$VarFlow2 = 2.0,
  [int]$VarSec2 = 15,
  [string]$MethodLabel = 'current',
  # Ramp test parameters
  [double]$RampStart = 0.0,
  [double]$RampEnd = 2.0,
  [int]$RampSeconds = 60,
  [switch]$RestartFirst,
  [string]$NumberEntityId = 'number.water_monitor_synth_synthetic_flow_gpm',
  [string]$LastSensorEntityId = 'sensor.water_monitor_synth_last_session_volume'
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

function Get-SynthEntityId {
  if ($NumberEntityId) { return $NumberEntityId }
  return 'number.water_monitor_synth_synthetic_flow_gpm'
}

function Wait-SynthEntity([int]$maxWaitSec = 60) {
  $deadline = (Get-Date).AddSeconds($maxWaitSec)
  do {
    $id = Get-SynthEntityId
    if ($id) {
      try { $st = Invoke-RestMethod -Method Get -Uri "$BaseUrl/api/states/$id" -Headers $hdrBasic; if ($st) { return $id } } catch {}
    }
    Start-Sleep -Seconds 1
  } while ((Get-Date) -lt $deadline)
  return (Get-SynthEntityId)
}

function Set-Synth([double]$v) {
  if (-not $script:SyntheticEntity) { $script:SyntheticEntity = Wait-SynthEntity }
  $body = @{ entity_id = $script:SyntheticEntity; value = $v } | ConvertTo-Json -Compress
  try {
    Invoke-RestMethod -Method Post -Uri "$BaseUrl/api/services/number/set_value" -Headers $hdrJson -Body $body | Out-Null
  } catch {
    Write-Warning "Failed to set synthetic: entity=$script:SyntheticEntity value=$v"
    if ($_.Exception.Response) {
      $sr = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream()); $txt = $sr.ReadToEnd(); $sr.Close();
      Write-Host $txt
    } else {
      Write-Host $_
    }
    throw
  }
}

function Get-Last() {
  $id = $LastSensorEntityId
  Invoke-RestMethod -Method Get -Uri "$BaseUrl/api/states/$id" -Headers $hdrBasic
}

function Wait-Finalize([string]$prevEnd, [int]$maxWaitSec = 45) {
  $deadline = (Get-Date).AddSeconds($maxWaitSec)
  do {
    Start-Sleep -Seconds 1
    try {
      $st = Get-Last
    } catch {
      continue
    }
    $active = [bool]$st.attributes.current_session_active
    $gap = [bool]$st.attributes.gap_active
    $lend = [string]$st.attributes.last_session_end
    if (-not $active -and -not $gap -and $lend -and ($lend -ne $prevEnd)) {
      return $st
    }
  } while ((Get-Date) -lt $deadline)
  return $st
}

if ($RestartFirst) { Restart-HA }

Write-Host "Baseline zero... (entity: $((Wait-SynthEntity)))" -ForegroundColor Cyan
Set-Synth 0.0; Start-Sleep -Seconds 2

Write-Host "Test1: $FlowGpm gpm for $SteadySeconds s" -ForegroundColor Cyan
$prev1 = (Get-Last).attributes.last_session_end
Set-Synth $FlowGpm; Start-Sleep -Seconds $SteadySeconds; Set-Synth 0.0
$st1 = Wait-Finalize -prevEnd $prev1 -maxWaitSec 45
$vol1 = [double]$st1.state
$dur1 = [int]$st1.attributes.last_session_duration
$avg1 = [double]$st1.attributes.last_session_average_flow
$unit = $st1.attributes.volume_unit
$expVol1 = $FlowGpm * ($SteadySeconds/60.0)
$ok1 = ([math]::Abs($vol1 - $expVol1) -le $TolVol) -and ($dur1 -ge ($SteadySeconds-1) -and $dur1 -le ($SteadySeconds+2)) -and ([math]::Abs($avg1 - $FlowGpm) -le $TolAvg)
Write-Host ("Test1: vol={0:N3} dur={1}s avg={2:N2} unit={3} expectedVol={4:N3} ok={5}" -f $vol1,$dur1,$avg1,$unit,$expVol1,$ok1)
Write-Host ("Test1 debug: gaps={0} synth_added={1} last_synth={2} flow_used={3} detectors_flow={4}" -f `
  $st1.attributes.last_session_gapped_sessions, $st1.attributes.synthetic_volume_added, $st1.attributes.last_session_synthetic_volume, `
  $st1.attributes.flow_used_by_engine, $st1.attributes.detectors_flow)

Write-Host "Test2: $Cycles cycles of $OnSeconds s ON / $OffSeconds s OFF" -ForegroundColor Cyan
$prev2 = (Get-Last).attributes.last_session_end
for ($i=0; $i -lt $Cycles; $i++) {
  Set-Synth $FlowGpm; Start-Sleep -Seconds $OnSeconds; Set-Synth 0.0; Start-Sleep -Seconds $OffSeconds
}
$st2 = Wait-Finalize -prevEnd $prev2 -maxWaitSec 60
$vol2 = [double]$st2.state
$dur2 = [int]$st2.attributes.last_session_duration
$avg2 = [double]$st2.attributes.last_session_average_flow
$active2 = ($Cycles * $OnSeconds)
$expVol2 = $FlowGpm * ($active2/60.0)
$ok2 = ([math]::Abs($vol2 - $expVol2) -le $TolVol) -and ($dur2 -ge ($active2-2) -and $dur2 -le ($active2+2)) -and ([math]::Abs($avg2 - $FlowGpm) -le $TolAvg)
Write-Host ("Test2: vol={0:N3} dur={1}s avg={2:N2} expectedVol={3:N3} ok={4}" -f $vol2,$dur2,$avg2,$expVol2,$ok2)
Write-Host ("Test2 debug: gaps={0} synth_added={1} last_synth={2} flow_used={3} detectors_flow={4}" -f `
  $st2.attributes.last_session_gapped_sessions, $st2.attributes.synthetic_volume_added, $st2.attributes.last_session_synthetic_volume, `
  $st2.attributes.flow_used_by_engine, $st2.attributes.detectors_flow)

# Final reset
Set-Synth 0.0

# Test3: Varying flow segments (e.g., 1 gpm for 30s then 2 gpm for 15s)
Write-Host ("Test3: Varying flow ({0}): {1} gpm for {2}s, then {3} gpm for {4}s" -f $MethodLabel,$VarFlow1,$VarSec1,$VarFlow2,$VarSec2) -ForegroundColor Cyan
$prev3 = (Get-Last).attributes.last_session_end
Set-Synth $VarFlow1; Start-Sleep -Seconds $VarSec1
Set-Synth $VarFlow2; Start-Sleep -Seconds $VarSec2
Set-Synth 0.0
$st3 = Wait-Finalize -prevEnd $prev3 -maxWaitSec 60
$vol3 = [double]$st3.state
$dur3 = [int]$st3.attributes.last_session_duration
$avg3 = [double]$st3.attributes.last_session_average_flow
$unit3 = $st3.attributes.volume_unit
$active3 = $VarSec1 + $VarSec2
$expVol3 = $VarFlow1 * ($VarSec1/60.0) + $VarFlow2 * ($VarSec2/60.0)
$ok3 = ([math]::Abs($vol3 - $expVol3) -le $TolVol) -and ($dur3 -ge ($active3-2) -and $dur3 -le ($active3+2))
Write-Host ("Test3 ({0}): vol={1:N3} dur={2}s avg={3:N2} unit={4} expectedVol={5:N3} ok={6}" -f $MethodLabel,$vol3,$dur3,$avg3,$unit3,$expVol3,$ok3)
Write-Host ("Test3 debug: gaps={0} synth_added={1} last_synth={2} flow_used={3} detectors_flow={4}" -f `
  $st3.attributes.last_session_gapped_sessions, $st3.attributes.synthetic_volume_added, $st3.attributes.last_session_synthetic_volume, `
  $st3.attributes.flow_used_by_engine, $st3.attributes.detectors_flow)

# Test4: Linear ramp from RampStart to RampEnd over RampSeconds (offline compare: left vs trapezoidal)
Write-Host ("Test4: Ramp {0}→{1} gpm over {2}s (offline compare: left vs trapezoidal)" -f $RampStart,$RampEnd,$RampSeconds) -ForegroundColor Cyan
$prev4 = (Get-Last).attributes.last_session_end
$steps = [Math]::Max(1,$RampSeconds)
$dt = 1.0 # seconds per step
$leftVol = 0.0
$trapVol = 0.0
$prevFlow = $RampStart
for ($t=0; $t -lt $steps; $t++) {
  $f = $RampStart + ($RampEnd - $RampStart) * (($t+1) / $steps)
  # Drive synthetic at the end-of-interval flow value
  Set-Synth $f
  # Left Riemann uses previous flow over this interval
  $leftVol += $prevFlow * ($dt/60.0)
  # Trapezoidal uses average of previous and current
  $trapVol += (($prevFlow + $f) / 2.0) * ($dt/60.0)
  $prevFlow = $f
  Start-Sleep -Seconds $dt
}
Set-Synth 0.0
$st4 = Wait-Finalize -prevEnd $prev4 -maxWaitSec 90
$unit4 = $st4.attributes.volume_unit
Write-Host ("Test4 (offline): left={0:N3} {3}, trapezoidal={1:N3} {3}, delta={2:N3} {3}" -f $leftVol,$trapVol,([math]::Abs($leftVol-$trapVol)),$unit4)
