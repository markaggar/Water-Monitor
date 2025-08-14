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
