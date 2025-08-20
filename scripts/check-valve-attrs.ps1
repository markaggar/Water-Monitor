param(
  [string]$BaseUrl = $env:HA_BASE_URL,
  [string]$Token = $env:HA_TOKEN,
  [string[]]$Entities = @(
    'binary_sensor.water_monitor_synth_low_flow_leak',
    'binary_sensor.water_monitor_synth_tank_refill_leak',
    'binary_sensor.water_monitor_synth_intelligent_leak'
  )
)

if (-not $BaseUrl) { $BaseUrl = 'http://10.0.0.55:8123' }
if (-not $Token) { throw 'HA_TOKEN env var not set' }
$hdr = @{ Authorization = "Bearer $Token" }

foreach ($id in $Entities) {
  try {
    $s = Invoke-RestMethod -Uri "$BaseUrl/api/states/$id" -Headers $hdr -TimeoutSec 15 -ErrorAction Stop
  } catch {
    Write-Host ("{0}: ERROR querying state" -f $id) -ForegroundColor Red
    continue
  }
  $a = $s.attributes
  Write-Host $id -ForegroundColor Cyan
  foreach ($k in @('auto_shutoff_on_trigger','auto_shutoff_effective','auto_shutoff_valve_entity','valve_off')) {
    if ($a.PSObject.Properties.Name -contains $k) {
      $val = $a.$k
      $json = $val | ConvertTo-Json -Compress
      Write-Host ("  {0}: {1}" -f $k, $json)
    } else {
      Write-Host ("  {0}: <missing>" -f $k) -ForegroundColor Yellow
    }
  }
}
