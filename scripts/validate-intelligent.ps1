param(
  [string]$BaseUrl = $env:HA_BASE_URL,
  [string]$Token = $env:HA_TOKEN,
  [int]$SeedDays = 14,
  [switch]$IncludeIrrigation,
  [int]$Sensitivity = 100,                 # 0..100; 100 -> ~p90 threshold (fastest)
  [double]$TestFlowGpm = 1.0,              # synthetic flow used during test
  [int]$MaxOnWaitS = 180,                  # safety cap for waiting leak=on
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

function Set-Number([string]$entityId, [double]$v) {
  $body = @{ entity_id = $entityId; value = $v } | ConvertTo-Json -Compress
  Invoke-RestMethod -Method Post -Uri "$BaseUrl/api/services/number/set_value" -Headers $hdrJson -Body $body | Out-Null
}

function Set-Synth([double]$v) { Set-Number -entityId $NumberEntityId -v $v }

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

function Get-IntelStats($intelId) {
  try {
    $st = Get-State $intelId
    return [ordered]@{
      state = $st.state
      count = [int]$st.attributes.count
      bucket = $st.attributes.bucket_used
      baseline_ready = [bool]$st.attributes.baseline_ready
      eff_thr_s = [double]$st.attributes.effective_threshold_s
      reasons = $st.attributes.reasons
      flow_now = [double]$st.attributes.flow_now
      risk = [double]$st.attributes.risk
      elapsed_s = [int]$st.attributes.elapsed_s
      chosen_p = $st.attributes.chosen_percentile
    }
  } catch { return $null }
}

function Invoke-Service([string]$domain, [string]$service, [hashtable]$data) {
  $body = ($data | ConvertTo-Json -Compress)
  Invoke-RestMethod -Method Post -Uri "$BaseUrl/api/services/$domain/$service" -Headers $hdrJson -Body $body
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

# Resolve entities
$intelId = Find-EntityByName -nameLike '*Intelligent leak*' -fallbackLike 'binary_sensor.*intelligent*leak*'
if (-not $intelId) { throw "Could not find Intelligent leak binary sensor" }
$sensId = Find-EntityByName -nameLike '*Leak alert sensitivity*' -fallbackLike 'number.*leak*Sensitivity*'
Write-Host "Using: intelligent=$intelId sensitivity=$sensId number=$NumberEntityId" -ForegroundColor Cyan

# 1) Seed history via service (for all entries if no entry_id provided)
$simArgs = @{ days = $SeedDays; include_irrigation = [bool]$IncludeIrrigation }
Write-Host "Seeding history: days=$SeedDays include_irrigation=$([bool]$IncludeIrrigation)" -ForegroundColor Cyan
Invoke-Service -domain 'water_monitor' -service 'simulate_history' -data $simArgs | Out-Null

# If baseline isn't ready for the current context bucket, add more days until count>=10 (cap at ~56 extra days)
$targetCount = 10
for ($i=0; $i -lt 8; $i++) {
  # Prime an update so intelligent sensor refreshes its attributes
  Set-Synth 0.2; Start-Sleep -Seconds 2; Set-Synth 0.0; Start-Sleep -Seconds 2
  $stats = Get-IntelStats $intelId
  if ($stats -and $stats.count -ge $targetCount -and $stats.baseline_ready) { break }
  $more = 7
  Write-Host "Baseline not ready (count=$($stats.count), bucket=$($stats.bucket)). Adding $more days..." -ForegroundColor Yellow
  Invoke-Service -domain 'water_monitor' -service 'simulate_history' -data @{ days = $more; include_irrigation = [bool]$IncludeIrrigation } | Out-Null
}

# 2) Set high sensitivity for a lower threshold (p90)
if ($sensId) {
  Write-Host "Setting leak sensitivity to $Sensitivity" -ForegroundColor Cyan
  Set-Number -entityId $sensId -v $Sensitivity
}

# 3) Nudge updates and wait for baseline_ready + effective_threshold
Write-Host "Priming updates to fetch threshold..." -ForegroundColor Cyan
Set-Synth $TestFlowGpm
# hold 6s to ensure at least one 5s cadence tick
Start-Sleep -Seconds 6
$ok = Wait-Condition -timeoutS 60 -intervalS 2 -cond {
  try {
    $st = Get-State $intelId
    $br = [bool]$st.attributes.baseline_ready
    $thr = [double]$st.attributes.effective_threshold_s
    $fn = [double]$st.attributes.flow_now
    return ($thr -ge 1 -and $fn -gt 0 -and $br)
  } catch { return $false }
}
if (-not $ok) {
  # still proceed; fetch current attributes for diagnostics
  try { $st = Get-State $intelId; Write-Host "BaselineReady=$($st.attributes.baseline_ready) effective_threshold_s=$($st.attributes.effective_threshold_s) count=$($st.attributes.count) bucket=$($st.attributes.bucket_used) flow_now=$($st.attributes.flow_now)" -ForegroundColor Yellow } catch {}
}

# Verify synthetic is included in detectors
$primed = Get-IntelStats $intelId
if ($primed -and $primed.flow_now -le 0) {
  Write-Warning "flow_now is 0 during priming; ensure 'Include synthetic flow in detectors' is enabled."
}

# Read threshold and compute target run duration
$st0 = Get-State $intelId
$thresholdS = [int]([math]::Ceiling([double]($st0.attributes.effective_threshold_s)))
if (-not $thresholdS -or $thresholdS -lt 5) { $thresholdS = 30 }
# Cap extremely large thresholds to keep test bounded
$capS = [math]::Min([int]$MaxOnWaitS, [int]($thresholdS + 60))
Write-Host "Effective threshold ~${thresholdS}s; will drive flow for up to ${capS}s (count=$($st0.attributes.count) bucket=$($st0.attributes.bucket_used))" -ForegroundColor Cyan

# 4) Drive flow until Intelligent leak turns ON (or time cap)
$start = Get-Date
$turnedOn = $false
while (-not $turnedOn -and ((Get-Date) - $start).TotalSeconds -lt $capS) {
  Start-Sleep -Seconds 2
  $st = Get-State $intelId
  if ($st.state -eq 'on') { $turnedOn = $true; break }
}
$stOn = Get-State $intelId
$reasonsStr = ($stOn.attributes.reasons -join ',')
$passOn = ($stOn.state -eq 'on' -and $stOn.attributes.risk -ge 1 -and $reasonsStr -match 'elapsed>p')
Write-Host ("Intelligent: on={0} elapsed={1}s risk={2} chosen_p={3} thr={4}s reasons={5}" -f $turnedOn, $stOn.attributes.elapsed_s, $stOn.attributes.risk, $stOn.attributes.chosen_percentile, $stOn.attributes.effective_threshold_s, $reasonsStr) -ForegroundColor Green
if (-not $passOn) { Write-Warning "Did not reach ON within cap or reasons not as expected (baseline fallback may be active)." }

# 5) Stop flow and confirm it clears
Set-Synth 0.0
$cleared = Wait-Condition -timeoutS 60 -intervalS 2 -cond { try { (Get-State $intelId).state -eq 'off' } catch { $false } }
$stOff = Get-State $intelId
$passOff = ($stOff.state -eq 'off')
Write-Host ("Cleared={0} risk_now={1} elapsed_now={2}s" -f $cleared, $stOff.attributes.risk, $stOff.attributes.elapsed_s)

if ($passOn -and $passOff) {
  Write-Host "E2E Intelligent leak test: PASS" -ForegroundColor Green
} else {
  Write-Host "E2E Intelligent leak test: FAIL" -ForegroundColor Red
}
