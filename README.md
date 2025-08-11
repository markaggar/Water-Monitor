# Water Monitor

A Home Assistant custom integration for intelligent water usage monitoring with robust session tracking, gap handling, hot water analytics, and optional low-flow leak detection. Supports multiple instances, reconfiguration via the UI, and clean sensor naming to avoid collisions.

## Features

- Intelligent session detection
  - Automatically detects water usage sessions from flow/volume sensors
  - Smart gap handling and session continuation to avoid splitting single sessions
- Dual sensors
  - Last session volume: Shows the most recently completed session with metadata (rounded to 2 decimals)
  - Current session volume: Real-time view during active use, shows the intermediate volume during gaps, and resets to 0 when a session ends (rounded to 2 decimals)
- Hot water analytics
  - Tracks hot water time and percentage per session via an optional hot water binary sensor
- Optional low-flow leak detector (binary sensor)
  - Detects a continuous low-flow “dribble” and latches across nonzero usage
  - Only clears when flow stops for a configured time (optional safety clear on sustained high flow)
  - Fully optional: enable with a checkbox during setup or in Options; parameters are reconfigurable
- Reconfigurable via Options
  - Adjust sensors and thresholds at any time in the integration’s Configure dialog
  - Optional sensor name prefix for easy disambiguation in the UI
- Multi-instance safe
  - Add multiple instances with different sensors and thresholds
  - Stable unique IDs and device grouping per instance
- Reliable finalization
  - Periodic evaluation during gaps and session end windows ensures sessions finalize even when source sensors are idle

## Installation

Manual installation
1) Copy the custom_components/water_monitor/ directory into your Home Assistant config/custom_components/ folder
2) Restart Home Assistant
3) Go to Settings → Devices & Services → Add Integration
4) Search for “Water Monitor” and complete the setup

HACS
- Add this repository in HACS as a Custom repository under Integrations (see repo URL).
- Install “Water Monitor,” restart Home Assistant, and add the integration.

## Configuration

Setup page (step 1)
- Sensor Name Prefix
- Flow Rate Sensor
- Volume Sensor
- Hot Water Sensor (optional)
- Minimum Session Volume
- Minimum Session Duration (seconds)
- Gap Tolerance (seconds)
- Continuity Window (seconds)
- Create Low-flow leak sensor (checkbox)

If “Create Low-flow leak sensor” is checked, you’ll be presented with a second page:

Low-flow leak (step 2)
- Max low-flow threshold (e.g., 0.5 GPM)
- Seed low-flow duration (seconds)
- Leak persistence required to trigger (seconds)
- Clear after zero-flow (seconds)
- Counting mode: nonzero_wallclock or in_range_only
- Smoothing window (seconds)
- Cooldown after clear (seconds)
- Clear on sustained high flow (seconds; blank to disable)

Reconfiguration
- Open Settings → Devices & Services → Water Monitor → Configure.
- The low-flow leak sensor is optional and can be enabled/disabled at any time:
  - Enabling creates the binary sensor.
  - Disabling removes the binary sensor on reload.

Units
- Volume sensors determine unit display (gallons/liters) for both session sensors.
- Flow sensor units are reflected in the low-flow leak sensor attributes.

## Sensors created

- Last session volume (sensor)
  - State: Last completed session volume (rounded to 2 decimals)
  - Attributes: durations, averages, hot-water percentage, flow_sensor_value, debug_state, and live tracker fields
- Current session volume (sensor)
  - State: Live session volume; during gaps shows intermediate volume; after finalization resets to 0 (rounded to 2 decimals)
  - Attributes: session_stage, session_duration, session_average_flow, session_hot_water_pct, raw values
- Low-flow leak (binary_sensor, optional)
  - State: on/off (device_class: problem)
  - Latches across nonzero usage and clears only after true zero-flow for the configured duration
  - Attributes: current_flow, thresholds, timers, and timestamps

## How it works

Session detection
1) Session starts when flow goes from 0 to > 0
2) Within-session gaps are tolerated up to Gap Tolerance
3) After flow stops, the Continuity Window ensures short pauses don’t end the session prematurely
4) The session finalizes after the continuity window elapses with no resumed flow
5) The session is recorded if it meets minimum volume and duration thresholds

Low-flow leak basics
- A low-flow baseline is established after a seed low-flow duration.
- Once seeded, the sensor counts persistence toward trigger:
  - nonzero_wallclock: count wall-clock time whenever flow > 0 (default)
  - in_range_only: count only while flow is within the low-flow threshold
- The sensor latches on across nonzero usage and clears only after zero-flow for the configured time.

Hot water
- Provide an optional binary sensor that reflects when hot water is active
- Hot water time is accumulated during active sessions and summarized as a percentage

## Troubleshooting

No entities created
- Ensure flow/volume sensors are set
- If low-flow leak is enabled, make sure a flow sensor is selected

Low-flow leak not triggering
- Reduce the max low-flow threshold or the seed/persistence durations
- Increase smoothing to stabilize noisy meters

Low-flow leak not clearing
- Verify the “Clear after zero-flow” duration; the sensor only clears after true zero flow

Debug logging
```yaml
logger:
  logs:
    custom_components.water_monitor: debug
```

## License

MIT License. See LICENSE.
