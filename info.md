# Water Monitor

A Home Assistant custom integration for intelligent water usage monitoring with robust session tracking, gap handling, hot water analytics, and optional leak detection. Supports multiple instances, reconfiguration via the UI, and clean sensor naming to avoid collisions.

<img width="256" height="256" alt="icon" src="https://github.com/user-attachments/assets/acbfb0a0-882a-4ad6-9ed6-f97f6c3c9194" />

## Features

- Intelligent session detection
  - Automatically detects water usage sessions from flow/volume sensors
  - Smart gap handling to avoid splitting single sessions
- Session sensors
  - Last session volume: Most recently completed session with metadata (rounded to 2 decimals)
  - Current session volume: Real-time view during active use, shows the intermediate volume during gaps, and resets to 0 when a session ends (rounded to 2 decimals)
  - Last session duration: Duration in seconds of the last completed session
  - Last session average flow: Average flow rate of the last completed session (<volume_unit>/min)
  - Last session hot water percentage: Hot water percentage of the last completed session
- Hot water analytics
  - Tracks hot water time and percentage per session via an optional hot water binary sensor
- Optional low-flow leak detector (binary sensor)
  - Detects a continuous low-flow “dribble” and latches across nonzero usage
  - Clears only after zero-flow for a configured time (optional safety clear on sustained high flow)
- Optional tank refill leak detector (binary sensor)
  - Detects repeated, similar-sized refills clustered in time (typical symptom of a leaky toilet flapper)
  - Event-driven: subscribes to the integration’s last session (no polling)
- Reconfigurable via Options
  - Adjust sensors and thresholds at any time in the Configure dialog
  - Optional sensor name prefix for easy disambiguation
- Multi-instance safe
  - Add multiple instances with different sensors and thresholds
  - Stable unique IDs and device grouping per instance
- Reliable finalization
  - Periodic evaluation during gaps and session end windows ensures sessions finalize even when source sensors are idle

## Installation

HACS (recommended)
1) In HACS → Integrations → three‑dot menu → Custom repositories → add:
   - https://github.com/markaggar/Water-Monitor (category: Integration)
2) Search for “Water Monitor,” install, and restart Home Assistant
3) Settings → Devices & Services → Add Integration → “Water Monitor”

Manual
- Copy custom_components/water_monitor/ into your config/custom_components/ directory
- Restart Home Assistant and add the integration

See README.md for detailed information

## Links

- Releases: https://github.com/markaggar/Water-Monitor/releases
- Source: https://github.com/markaggar/Water-Monitor

## License

MIT
