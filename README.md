# Water Monitor

<img width="256" height="256" alt="icon" src="https://github.com/user-attachments/assets/f3bbd8f3-52f9-4676-b80b-fd601021192c" />

A Home Assistant custom integration for intelligent water usage monitoring with robust session tracking, gap handling, hot water analytics, and optional leak detection. Only a Flow sensor is required; a Volume sensor is optional. If you do supply a Volume sensor, Water Monitor will use it directly (ideal if you want volumes to align with the Energy dashboard). Supports multiple instances, reconfiguration via the UI, and clean sensor naming to avoid collisions.

<img width="449" height="666" alt="image" src="https://github.com/user-attachments/assets/a8cdcfeb-f03d-4e9c-9527-e7230c58ddd8" />

## Features

- Intelligent session detection
  - Automatically detects water usage sessions from flow/volume sensors
  - Smart gap handling to avoid splitting single sessions
- Session sensors
  - Last session volume: Most recently completed session with metadata
  - Current session volume: Real-time view during active use, shows the intermediate volume during gaps, and resets to 0 when a session ends
  - Last session duration: Duration in seconds of the last completed session
  - Last session average flow: Average flow rate of the last completed session (volume unit per minute)
  - Last session hot water percentage: Hot water percentage of the last completed session
- Hot water analytics
  - Tracks hot water time and percentage per session via an optional hot water binary sensor
- Optional low-flow leak detector
  - Detects a continuous low-flow “dribble” with seed/persistence timers
- Optional tank refill leak detector
  - Detects repeated, similar-sized refills clustered in time (typical symptom of a leaky toilet flapper)
- Upstream sensors health (binary sensor)
  - Monitors availability/validity of the configured upstream sensors (flow, volume, and optional hot-water)
- Reconfigurable via Options
  - Adjust sensors and thresholds at any time in the integration’s Configure dialog
  - Optional sensor name prefix for easy disambiguation in the UI
- Multi-instance safe
  - Add multiple instances with different sensors and thresholds
- Synthetic flow testing support
  - Optional integration-owned number to inject synthetic GPM for testing (no need to waste actual water).

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

<img width="364" height="920" alt="image" src="https://github.com/user-attachments/assets/0c773851-d4fa-4782-8683-b673e0701524" />

Setup page (step 1)
- Sensor Name Prefix
- Flow Rate Sensor (required)
- Volume Sensor (optional; if provided, Water Monitor uses it as the source of truth. If omitted, Water Monitor computes volume from the flow sensor.)
- Hot Water Sensor (optional)
- Minimum Session Volume
- Minimum Session Duration (seconds)
- Gap Tolerance (seconds)
- Treat baseline threshold as zero for session end (checkbox)
- Baseline idle to close session (seconds)
- Create Low-flow leak sensor (checkbox)
- Create Tank refill leak sensor (checkbox)
- Enable Intelligent Leak Detection (experimental) (checkbox)

If “Create Low-flow leak sensor” is checked, you’ll be presented with a second page:

Low-flow leak (step 2)
- Max low-flow threshold (e.g., 0.5 GPM)
- Seed low-flow duration (seconds)
- Leak persistence required to trigger (seconds)
- Clear after zero-flow (seconds)
- Counting mode: nonzero_wallclock, in_range_only, or baseline_latch (preview)
- Baseline margin (%) (used by baseline_latch)
- Smoothing window (seconds)
- Cooldown after clear (seconds)
- Clear on sustained high flow (seconds; blank to disable)

If “Create tank refill leak sensor” is checked, you’ll be presented with a third page:

Tank refill leak (step 2)
- Minimum refill volume (ignore refills smaller than this)
- Maximum refill volume (ignore refills larger than this; 0 disables the cap)
- Similarity tolerance (%) — how close in volume refills must be to count as “similar”
- Repeat count to trigger — number of similar refills within the window required to turn the sensor on
- Window to count repeats (seconds)
- Auto-clear after idle (seconds) — clears after this period with no matching refills
- Cooldown after clear (seconds) — optional suppression period before re-triggering
- Minimum refill duration (seconds; 0 disables)
- Maximum refill duration (seconds; 0 disables)

If “Enable Intelligent Leak Detection” is checked, you’ll be presented with another page:

Intelligent Leak Detection (experimental)
- Occupancy mode input_select (optional)
- Away states (comma-separated, optional)
- Vacation states (comma-separated, optional)
- Enable learning mode (toggle)

Notes

- CSV fields accept multiple labels separated by commas, e.g. "On Vacation, Returning from Vacation".
- Learning mode is intended for future automation-assisted tuning; you can toggle it via Options or automations.

Reconfiguration

- Open Settings → Devices & Services → Water Monitor → Configure.
- The low-flow leak sensor is optional and can be enabled/disabled at any time:
  - Enabling creates the binary sensor.
  - Disabling removes the binary sensor on reload.

Units

- If a Volume sensor is configured, its unit determines display (gallons/liters) and ensures alignment with the Energy dashboard.
- If no Volume sensor is configured, Water Monitor computes volume by integrating the Flow sensor (default method: Trapezoidal; alternative: Left to match external counters). Units are inferred from the Flow sensor (e.g., GPM → gal, L/min → L).
- Flow sensor units are reflected in the low-flow leak sensor attributes and used for last-session average flow when possible.
- Average flow sensor displays as <volume_unit>/min derived from the volume unit.

## Sensors created

- Last session volume (sensor)
  - State: Last completed session volume (rounded to 2 decimals)
  - Attributes: durations, averages, hot-water percentage, integration_method (external_volume_sensor | trapezoidal | left), sampling_active_seconds, sampling_gap_seconds, flow_sensor_value, debug_state, and live tracker fields
- Current session volume (sensor)
  - State: Live session volume; during gaps shows intermediate volume; after finalization resets to 0 (rounded to 2 decimals)
  - Attributes: session_stage, session_duration, session_average_flow, session_hot_water_pct, raw values
- Last session duration (sensor)
  - State: Seconds (integer) of the last completed session
  - Attributes: debug_state
- Last session average flow (sensor)
  - State: Average flow of the last session; unit is <volume_unit>/min (rounded to 2 decimals)
  - Attributes: volume_unit, debug_state
- Last session hot water percentage (sensor)
  - State: Percentage of hot water time in the last session (rounded to 0.1)
  - Attributes: debug_state
- Low-flow leak (binary_sensor, optional)
  - State: on/off (device_class: problem)
  - Seeds after continuous low-flow, then triggers after required persistence; clears after zero-flow and/or sustained high flow
  - Attributes (highlights):
    - mode, phase
    - flow, max_low_flow
    - seed_required_s, seed_progress_s
    - min_duration_s, count_progress_s
    - idle_zero_s, high_flow_s
    - clear_idle_s, clear_on_high_s, cooldown_s, cooldown_until
    - smoothing_s, baseline_margin_pct
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
    - min_refill_duration_s, max_refill_duration_s
    - contributing_events: array of {ts, volume, duration_s}
- Upstream sensors health (binary_sensor)
  - State: on/off (device_class: connectivity)
  - Attributes: unavailable_entities, unknown_entities, name_to_entity, and per-entity last OK timestamps

## How it works

### Session detection

1) Session starts when flow goes from 0 to >0
2) Within-session gaps are tolerated up to Gap Tolerance
3) After flow stops, the session remains open for the Gap Tolerance; if flow resumes within the tolerance, it’s one session
4) The session finalizes once the gap tolerance elapses with no resumed flow
5) Duration and averages exclude gap time (only periods with non-zero flow count toward duration and average flow)
6) The session is recorded if it meets minimum volume and duration thresholds

Gap handling example

```text
Flow: ████████░░░████████░██████████░░░░░░░░░░░░
Time: 0--5--10--15--20--25--30--35--40--45--50s
  │              │              │
  Session Start  │              Session End
         │              (gap tolerance elapsed)
         Brief 2s gap
         (within tolerance)

Duration counts only the solid blocks (non-zero flow):
     [========]    [========]   [===========]
     ^ active ^    ^ active ^   ^  active  ^
  (gap time excluded from duration and averages)
```

### Last session volume

- State: Volume of the most recent completed session
- Attributes (highlights):
  - last_session_duration: Seconds
  - last_session_average_flow: Average flow rate (derived over active time; gap time excluded)
  - last_session_hot_water_pct: Percentage of time hot water was active
  - last_session_gapped_sessions: Number of gaps bridged during the session
  - flow_sensor_value: Instantaneous flow rate from your flow sensor
  - debug_state: ACTIVE, GAP, or IDLE (helps with troubleshooting)
  - Note: Attributes also include current and intermediate fields that reflect the tracker’s live state machine.

### Current session volume

- State: Live session volume while water is in use; during a gap, shows the intermediate (snapshot) volume; after finalization, resets to 0
- Attributes (triaged to match the most relevant stage: current → intermediate → final):
  - session_stage: current | intermediate | final
  - session_duration: Seconds (from the selected stage; excludes gap time)
  - session_average_flow: Derived average flow (from the selected stage; excludes gap time)
  - session_hot_water_pct: Hot water percentage (from the selected stage)
  - flow_sensor_value: Instantaneous flow rate
  - Plus raw values for transparency:
    - current_session_volume, current_session_duration, current_session_average_flow, current_session_hot_water_pct
    - intermediate_session_volume, intermediate_session_duration, intermediate_session_average_flow, intermediate_session_hot_water_pct
    - last_session_volume, last_session_duration, last_session_average_flow, last_session_hot_water_pct

Notes

- The Current session volume sensor resets to 0 when a session ends, while keeping attributes that summarize the most relevant stage.
- The Last session volume sensor’s state updates only after a session completes and passes thresholds.

### Last session duration

- State: last_session_duration (seconds)
- Attributes: debug_state

### Last session average flow

- State: last_session_average_flow (volume unit per minute), rounded to 2 decimals (computed over active time; gap time excluded)
- Attributes: volume_unit (inferred from source volume sensor), debug_state

### Last session hot water percentage

- State: last_session_hot_water_pct (%), rounded to 0.1
- Attributes: debug_state

## Leak Detector Sensors

### Low-flow leak basics

- Seeding: continuous low-flow must persist for the configured seed duration before counting begins (seed_s).
- Once seeded, the sensor counts persistence toward trigger (min_s):
  - nonzero_wallclock: counts wall-clock time whenever flow > 0 (default)
  - in_range_only: counts only while 0 < flow ≤ max low-flow threshold
  - baseline_latch (preview): currently behaves like in_range_only; a full baseline-latch implementation is planned
- Clearing: after true zero-flow idle for clear_idle_s and/or after sustained high flow for clear_on_high_s; optional cooldown delays re-triggering.

### Tank refill leak basics

- What it detects: clustered, similar-sized refills (e.g., multiple toilet tank refills) that suggest a slow leak.
- Event source: the integration’s “Last session volume” updates; the tank sensor listens for completed sessions and examines their volumes.
- Similarity: two refills are considered “similar” if the absolute difference is within the configured similarity tolerance percentage of the latest refill.
- Trigger: when the number of similar refills within the configured window reaches the repeat count.
- Clearing: when no similar refills occur for the configured idle period; optional cooldown prevents immediate re-triggering.
- Guards: refills below Minimum/shorter than Min duration or above Maximum/longer than Max duration (if set) are ignored to avoid false positives from noise or large draws unrelated to tank refills.

Tuning tips

- Minimum refill volume: set just below your typical toilet refill to ignore tiny noise.
- Maximum refill volume: set just above a toilet refill to ignore showers/sprinklers; set 0 to disable the cap.
- Similarity tolerance: start around 10%; increase if your meter reports variable volumes per flush.
- Repeat count and window: choose how many similar refills within what time should indicate a leak (e.g., 3 within 15 minutes).

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
  - entity: sensor.last_session_duration      # replace with your actual entity_id
    name: "Last Session Duration"
  - entity: sensor.last_session_average_flow  # replace with your actual entity_id
    name: "Last Session Average Flow"
  - entity: sensor.last_session_hot_water_percentage  # replace with your actual entity_id
    name: "Last Session Hot Water %"

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

- Tune Gap Tolerance to your plumbing and sensor update frequency

### No entities created

- Ensure flow/volume sensors are set
- If low-flow leak is enabled, make sure a flow sensor is selected
  - Derived sensors (duration, average flow, hot water %) are created automatically and stay in sync with the last-session sensor.
  - If you removed and re-added the integration, re-enable low-flow/tank leak options in the setup steps to recreate their binary sensors.

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

## Changelog

### 0.3.0

- New: Synthetic flow (gpm) test control as a Number entity
  - Options to include synthetic flow in detectors and/or the engine’s live calculations
  - Last/Current session sensors can include synthetic gallons when enabled, useful for simulation
- Engine behavior: Synthetic gallons are excluded from stored sessions and daily analysis (analyze_yesterday)
  - Daily totals and anomaly thresholds are computed without synthetic, so testing doesn’t skew analytics
  - Sessions that are purely synthetic are not recorded by the engine
- Stability: Improved session finalization logic and attributes for better visibility
- Session model: Removed the separate continuity window; a single Gap Tolerance governs within-session gaps and finalization. Session duration and averages now exclude gap time.
