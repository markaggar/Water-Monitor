param(
  [string]$BaseUrl = $env:HA_BASE_URL,
  [string]$Token = $env:HA_TOKEN,
  [string[]]$Entities = @(
    'binary_sensor.water_monitor_synth_intelligent_leak',
    'binary_sensor.water_monitor_synth_low_flow_leak',
    'binary_sensor.water_monitor_synth_tank_refill_leak'
  )
)

if (-not $BaseUrl) { $BaseUrl = "http://10.0.0.55:8123" }
if (-not $Token) { throw "HA_TOKEN env var not set" }
$hdr = @{ Authorization = "Bearer $Token" }

function Show-Attrs([string]$id) {
  try {
    $s = Invoke-RestMethod -Uri "$BaseUrl/api/states/$id" -Headers $hdr
  } catch {
    Write-Host "${id}: ERROR querying state" -ForegroundColor Red
    return
  }
  $a = $s.attributes
  $hasDet = $null -ne $a.detectors_includes_synthetic
  $hasPct = $null -ne $a.synthetic_flow_pct -or $null -ne $a.last_event_synthetic_pct
  $hasContrib = $null -ne $a.synthetic_contribution
  Write-Host ("{0}: detectors={1} pct={2} contrib={3}" -f $id, $hasDet, $hasPct, $hasContrib)
  if ($hasContrib) {
    $c = $a.synthetic_contribution
    Write-Host ("  contribution: type={0} pct={1} value={2} {3}" -f $c.type, $c.pct, $c.value, $c.unit)
  }
}

foreach ($id in $Entities) { Show-Attrs $id }
