# Simulation scripts for Water Monitor

## Prereqs

- Environment variables:
  - HA_BASE_URL (e.g., <http://10.0.0.55:8123>)
  - HA_TOKEN (long-lived access token)
  - Optional: FLOW_ENTITY (defaults to input_number.water_flow_gpm)

- Ensure your Water Monitor integration is driven by a flow source that reflects FLOW_ENTITY. A common setup is:
  - input_number (FLOW_ENTITY) -> template/triggered sensor -> integration sensor (volume) -> Water Monitor.

## Scripts

- simulate_random_usage.py
  - Spawns parallel threads simulating faucets, showers, washer, toilet, dishwasher, irrigation.
  - Each event adds its delta to FLOW_ENTITY on start and subtracts it on finish. Durations/intervals are randomized.
  - Stop with Ctrl+C; active events will subtract their deltas on shutdown.

- simulate_tank_leak.py
  - Adds a steady leak baseline (LEAK_DELTA) and keeps it until stopped. Adds small jitter periodically.
  - Env overrides: LEAK_DELTA (default 0.2), JITTER (0.05), JITTER_INTERVAL seconds (15).

## Tips

- You can run both scripts at once; flow adjusts additively. When one stops, flow reduces by that script's contribution.
- Use SIM_RNG_SEED to make the random usage deterministic when needed.

## Troubleshooting

- If requests fail, check HA_BASE_URL/HA_TOKEN.
- If no changes in HA, verify FLOW_ENTITY maps to your flow source (e.g., input_number) and the template/integration wiring is correct.

# Deployment helper scripts

- `deploy-water-monitor.ps1` — copies `custom_components/water_monitor` to your Home Assistant share (default: `\\10.0.0.55\config\custom_components\water_monitor`).

## Optional Git hook

This repo includes `.githooks/pre-push` that calls the deploy script after you push. To enable it, run:

```powershell
git config core.hooksPath .githooks
```

Notes:

- The hook is best-effort and won’t block your push on copy failures.
- Adjust the destination by passing `-DestPath` or editing the script.
