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
- Optional tank refill leak detector (binary sensor)
  - Detects repeated, similar-sized refills clustered in time (typical symptom of a leaky toilet flapper)
  - Event-driven: subscribes to the integration’s last session, no polling
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
- Create Tank refill leak sensor (checkbox)

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

Tank refill leak (step 2)
- Minimum refill volume (ignore refills smaller than this)
- Maximum refill volume (ignore refills larger than this; 0 disables the cap)
- Similarity tolerance (%) — how close in volume refills must be to count as “similar”
- Repeat count to trigger — number of similar refills within the window required to turn the sensor on
- Window to count repeats (seconds)
- Auto-clear after idle (seconds) — clears after this period with no matching refills
- Cooldown after clear (seconds) — optional suppression period before re-triggering

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
- Tank refill leak (binary_sensor, optional)
  - State: on/off (device_class: problem)
  - Detects repeated, similar refill events within a time window
  - Attributes (highlights):
    - events_in_window: Number of refill events in the current window
    - similar_count: Count of events similar to the latest refill (within tolerance)
    - min_refill_volume / max_refill_volume
    - tolerance_pct, repeat_count, window_s
    - clear_idle_s, cooldown_s
    - last_event: ISO timestamp of last considered refill

## How it works

### Session detection
1) Session starts when flow goes from 0 to >0
2) Within-session gaps are tolerated up to Gap Tolerance
3) After flow stops, the Continuity Window ensures short pauses don’t end the session prematurely
4) The session finalizes after the continuity window elapses with no resumed flow
5) The session is recorded if it meets minimum volume and duration thresholds

Gap handling example
```
Flow: ████████░░░████████░██████████░░░░░░░░░░░░
Time: 0--5--10--15--20--25--30--35--40--45--50s
      │              │              │
      Session Start  │              Session End
                     │              (gap + continuity elapsed)
                     Brief 2s gap
                     (within tolerance)
```

### Last session volume
- State: Volume of the most recent completed session
- Attributes (highlights):
  - last_session_duration: Seconds
  - last_session_average_flow: Average flow rate (derived)
  - last_session_hot_water_pct: Percentage of time hot water was active
  - last_session_gapped_sessions: Number of gaps bridged during the session
  - flow_sensor_value: Instantaneous flow rate from your flow sensor
  - debug_state: ACTIVE, GAP, or IDLE (helps with troubleshooting)
  - Note: Attributes also include current and intermediate fields that reflect the tracker’s live state machine.

### Current session volume
- State: Live session volume while water is in use; during a gap, shows the intermediate (snapshot) volume; after finalization, resets to 0
- Attributes (triaged to match the most relevant stage: current → intermediate → final):
  - session_stage: current | intermediate | final
  - session_duration: Seconds (from the selected stage)
  - session_average_flow: Derived average flow (from the selected stage)
  - session_hot_water_pct: Hot water percentage (from the selected stage)
  - flow_sensor_value: Instantaneous flow rate
  - Plus raw values for transparency:
    - current_session_volume, current_session_duration, current_session_average_flow, current_session_hot_water_pct
    - intermediate_session_volume, intermediate_session_duration, intermediate_session_average_flow, intermediate_session_hot_water_pct
    - last_session_volume, last_session_duration, last_session_average_flow, last_session_hot_water_pct

Notes
- The Current session volume sensor resets to 0 when a session ends, while keeping attributes that summarize the most relevant stage.
- The Last session volume sensor’s state updates only after a session completes and passes thresholds.

### Low-flow leak basics
- A low-flow baseline is established after a seed low-flow duration.
- Once seeded, the sensor counts persistence toward trigger:
  - nonzero_wallclock: count wall-clock time whenever flow > 0 (default)
  - in_range_only: count only while flow is within the low-flow threshold
- The sensor latches on across nonzero usage and clears only after zero-flow for the configured time.

### Tank refill leak basics
- What it detects: clustered, similar-sized refills (e.g., multiple toilet tank refills) that suggest a slow leak.
- Event source: the integration’s “Last session volume” updates; the tank sensor listens for completed sessions and examines their volumes.
- Similarity: two refills are considered “similar” if the absolute difference is within the configured similarity tolerance percentage of the latest refill.
- Trigger: when the number of similar refills within the configured window reaches the repeat count.
- Clearing: when no similar refills occur for the configured idle period; optional cooldown prevents immediate re-triggering.
- Guards: refills below Minimum or above Maximum (if set) are ignored to avoid false positives from noise or large draws unrelated to tank refills.

Tuning tips
- Minimum refill volume: set just below your typical toilet refill to ignore tiny noise.
- Maximum refill volume: set just above a toilet refill to ignore showers/sprinklers; set 0 to disable the cap.
- Similarity tolerance: start around 10%; increase if your meter reports variable volumes per flush.
- Repeat count and window: choose how many similar refills within what time should indicate a leak (e.g., 3 within 15 minutes).

### Hot water
- Provide an optional binary sensor that reflects when hot water is active
- Hot water time is accumulated during active sessions and summarized as a percentage

## Examples

### Automations (templates use your actual entity IDs)
```yaml
# Example: Notify when a large session completes
automation:
  - alias: "High Water Usage Alert"
    trigger:
      - platform: state
        entity_id: sensor.last_session_volume  # replace with your actual entity_id
    condition:
      - condition: numeric_state
        entity_id: sensor.last_session_volume  # replace with your actual entity_id
        above: 50  # example threshold, units follow your source sensors
    action:
      - service: notify.mobile_app_phone
        data:
          message: "High water usage: {{ states('sensor.last_session_volume') }} used in last session."
```

### Dashboards
```yaml
type: entities
title: Water Monitoring
entities:
  - entity: sensor.last_session_volume      # replace with your actual entity_id
    name: "Last Session Volume"
    secondary_info: attribute
    attribute: last_session_duration
  - entity: sensor.current_session_volume   # replace with your actual entity_id
    name: "Current Session Volume"

# Optional binary sensors (if enabled)
  - entity: binary_sensor.water_monitor_tank_refill_leak   # example name
    name: "Tank Refill Leak"
  - entity: binary_sensor.water_monitor_low_flow_leak      # example name
    name: "Low-flow Leak"
```

## Compatibility

- Home Assistant: 2024.1.0+
- Python: 3.11+
- Flow sensors: Any sensor providing flow rate (GPM/LPM)
- Volume sensors: Any sensor providing cumulative volume (Gallons/Liters)
- Hot water: Any binary sensor (on/off)

## Troubleshooting

### Integration not appearing
- Ensure files are in: config/custom_components/water_monitor/
- Restart Home Assistant
- Check logs for errors

### Sessions not being detected
- Verify flow and volume sensors are providing numeric values
- Confirm flow changes from 0 to a positive value
- Adjust minimum volume/duration thresholds if needed

### Gaps not handled as expected
- Tune Gap Tolerance and Continuity Window to your plumbing and sensor update frequency

### No entities created
- Ensure flow/volume sensors are set
- If low-flow leak is enabled, make sure a flow sensor is selected

### Low-flow leak not triggering
- Reduce the max low-flow threshold or the seed/persistence durations
- Increase smoothing to stabilize noisy meters

### Low-flow leak not clearing
- Verify the “Clear after zero-flow” duration; the sensor only clears after true zero flow

### Translations not updating
- The frontend caches translations. If labels show as raw keys, clear your browser cache or restart Home Assistant.

## Debug logging
```yaml
logger:
  logs:
    custom_components.water_monitor: debug
```

## Contributing

Issues and PRs are welcome. Please open an issue to discuss larger changes.


## License

MIT License. See LICENSE.
