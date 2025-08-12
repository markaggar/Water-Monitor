# Water Monitor

A Home Assistant custom integration for intelligent water usage monitoring with robust session tracking, gap handling, hot water analytics, and optional leak detection. Supports multiple instances, reconfiguration via the UI, and clean sensor naming to avoid collisions.

<img width="256" height="256" alt="icon" src="https://github.com/user-attachments/assets/acbfb0a0-882a-4ad6-9ed6-f97f6c3c9194" />

## Features

- Intelligent session detection
  - Automatically detects water usage sessions from flow/volume sensors
  - Smart gap handling and session continuation to avoid splitting single sessions
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

## Configuration

<img width="502" height="938" alt="image" src="https://github.com/user-attachments/assets/9da5a4c4-6e68-4edc-8275-250867d11746" />

Setup page (step 1)
- Sensor Name Prefix
- Flow Rate Sensor
- Volume Sensor
- Hot Water Sensor (optional)
- Minimum Session Volume
- Minimum Session Duration (seconds)
- Gap Tolerance (seconds)
- Continuity Window (seconds)
- Create Low‑flow leak sensor (checkbox)
- Create Tank refill leak sensor (checkbox)

If “Create Low‑flow leak sensor” is checked, you’ll be presented with a second page:

<img width="522" height="935" alt="image" src="https://github.com/user-attachments/assets/2f86f117-754a-47bb-8421-6d418cff6638" />

Low‑flow leak (step 2)
- Max low‑flow threshold (e.g., 0.5 GPM)
- Seed low‑flow duration (seconds)
- Leak persistence required to trigger (seconds)
- Clear after zero‑flow (seconds)
- Counting mode: nonzero_wallclock or in_range_only
- Smoothing window (seconds)
- Cooldown after clear (seconds)
- Clear on sustained high flow (seconds; blank to disable)

<img width="455" height="904" alt="image" src="https://github.com/user-attachments/assets/a372b30c-edda-4566-b33e-3060facfa654" />

Tank refill leak (step 2)
- Minimum refill volume (ignore smaller)
- Maximum refill volume (ignore larger; 0 disables the cap)
- Similarity tolerance (%) — how close in volume refills must be to count as “similar”
- Repeat count to trigger — number of similar refills within the window to turn on
- Window to count repeats (seconds)
- Auto‑clear after idle (seconds)
- Cooldown after clear (seconds)

Reconfiguration
- Settings → Devices & Services → Water Monitor → Configure
- Both leak sensors are optional and can be enabled/disabled at any time:
  - Enabling creates the binary sensor
  - Disabling removes it on reload

Units
- Volume unit is derived from your upstream volume sensor (gallons/liters)
- Flow units are reflected in leak sensor attributes
- Average flow displays as <volume_unit>/min derived from the source volume unit

## Sensors created

- Last session volume (sensor)
  - State: Last completed session volume (rounded to 2 decimals)
  - Attributes: durations, averages, hot‑water percentage, flow_sensor_value, debug_state, and live tracker fields
- Current session volume (sensor)
  - State: Live volume; during gaps shows intermediate volume; after finalization resets to 0 (rounded to 2 decimals)
  - Attributes: session_stage, session_duration, session_average_flow, session_hot_water_pct, raw values
- Last session duration (sensor)
  - State: Seconds (integer)
- Last session average flow (sensor)
  - State: <volume_unit>/min (rounded to 2 decimals)
- Last session hot water percentage (sensor)
  - State: % (rounded to 0.1)
- Low‑flow leak (binary_sensor, optional)
  - device_class: problem; latches across nonzero usage; clears after zero‑flow duration
- Tank refill leak (binary_sensor, optional)
  - device_class: problem; detects repeated, similar refills within a time window

## How it works (brief)

- Session starts when flow rises above zero
- Within‑session gaps are tolerated up to Gap Tolerance
- After flow stops, the Continuity Window avoids premature finalization
- Session finalizes after the window elapses with no resumed flow and passes thresholds

## Troubleshooting

- HACS shows a hex value as version
  - This indicates a commit‑based install. Reinstall from “Releases” and select a tag (e.g., 0.1.7+).
  - In HACS: integration card → three‑dot menu → Reinstall → Version → select release.
- Store page shows old content
  - HACS renders info.md when present. After adding it, HACS → three‑dot menu → Reload data, or restart Home Assistant.

## Debug logging
```yaml
logger:
  logs:
    custom_components.water_monitor: debug
```

## Links

- Releases: https://github.com/markaggar/Water-Monitor/releases
- Source: https://github.com/markaggar/Water-Monitor

## License

MIT
