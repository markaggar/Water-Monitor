# Water Monitor

A Home Assistant custom integration for intelligent water usage monitoring with robust session tracking, gap handling, hot water analytics, and optional leak detection. Only a Flow sensor is required; a Volume sensor is optional (used as the source of truth when provided). Multi‑instance friendly, fully configurable in the UI, and clean sensor naming to avoid collisions.

## Highlights

- Smart session detection with gap handling; duration and averages exclude gap time
- Works with Flow‑only (integrates volume for you) or with a Volume sensor (uses it directly)
- Transparent attributes: integration_method (external_volume_sensor | trapezoidal | left), sampling intervals, and more
- Hot water analytics via an optional binary sensor
- Optional leak detectors:
  - Low‑flow leak: detects continuous “dribble” with seed/persistence, smoothing, cooldown, and high‑flow clear
  - Tank refill leak: detects clustered, similar‑sized refills; contributing_events include local_time for notifications
- Upstream sensors health monitor (binary) for flow/volume/hot‑water sources
- Synthetic flow test control (Number) for safe, repeatable simulations

## Devices

- Sensors
  - Last session volume
  - Current session volume
  - Last session duration
  - Last session average flow
  - Last session hot water percentage
- Binary Sensors (optional)
  - Low‑flow leak
  - Tank refill leak
  - Upstream sensors health

## Learn more

- Full README: [github.com/markaggar/Water-Monitor](https://github.com/markaggar/Water-Monitor#readme)
- Releases: [github.com/markaggar/Water-Monitor/releases](https://github.com/markaggar/Water-Monitor/releases)
- Source: [github.com/markaggar/Water-Monitor](https://github.com/markaggar/Water-Monitor)
