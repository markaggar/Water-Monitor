param(
    [int]$Days = 7,
    [Nullable[int]]$Seed = $null,
    [bool]$IncludeIrrigation = $true,
    [string]$EntryId
)
$ErrorActionPreference = 'Stop'
if (-not $Env:HA_BASE_URL) { throw 'HA_BASE_URL env var not set' }
if (-not $Env:HA_TOKEN) { throw 'HA_TOKEN env var not set' }
$uri = "$Env:HA_BASE_URL/api/services/water_monitor/simulate_history"
$headers = @{ Authorization = "Bearer $Env:HA_TOKEN" }
$body = @{ days = $Days; include_irrigation = $IncludeIrrigation }
if ($Seed -ne $null) { $body.seed = [int]$Seed }
if ($EntryId) { $body.entry_id = $EntryId }
$payload = ($body | ConvertTo-Json)
Write-Host "POST $uri with days=$Days, include_irrigation=$IncludeIrrigation, seed=$Seed"
Invoke-RestMethod -Method Post -Uri $uri -Headers $headers -ContentType 'application/json' -Body $payload -TimeoutSec 120 | Out-Null
Write-Host 'Simulate request sent.'
