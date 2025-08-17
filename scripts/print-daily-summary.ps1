param(
    [string]$Date
)
$ErrorActionPreference = 'Stop'
if (-not $Date) { $Date = (Get-Date).AddDays(-1).ToString('yyyy-MM-dd') }
$store = "\\10.0.0.55\config\.storage"
Get-ChildItem $store -Filter 'water_monitor_*_engine.json' | ForEach-Object {
  try {
    $data = Get-Content $_.FullName -Raw | ConvertFrom-Json
    if ($null -ne $data.daily -and $data.daily.ContainsKey($Date)) {
      Write-Host "-- $($_.Name) --"
      $data.daily[$Date] | ConvertTo-Json -Depth 6
    } else {
      Write-Host "-- $($_.Name): No summary for $Date --"
    }
  } catch {
    Write-Warning "Failed reading $($_.Name): $_"
  }
}
