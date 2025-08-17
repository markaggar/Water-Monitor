param(
    [string]$EntryId
)
$ErrorActionPreference = 'Stop'
if (-not $Env:HA_BASE_URL) { throw 'HA_BASE_URL env var not set' }
if (-not $Env:HA_TOKEN) { throw 'HA_TOKEN env var not set' }
$uri = "$Env:HA_BASE_URL/api/services/water_monitor/analyze_yesterday"
$headers = @{ Authorization = "Bearer $Env:HA_TOKEN" }
$body = @{}
if ($EntryId) { $body.entry_id = $EntryId }
$payload = ($body | ConvertTo-Json)
Write-Host "POST $uri"
Invoke-RestMethod -Method Post -Uri $uri -Headers $headers -ContentType 'application/json' -Body $payload -TimeoutSec 60 | Out-Null
Write-Host 'Analyze request sent.'
