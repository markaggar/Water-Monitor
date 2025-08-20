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
- **NEW: Shutoff valve support**
  - Optionally link a shutoff valve entity (switch, input_boolean, or valve)
  - Per-detector auto-shutoff toggles: auto-shutoff can be enabled for each leak detector
  - When a leak is detected, the valve is turned off automatically (if enabled)
  - Leak sensors will not clear while the valve is off, ensuring you don't miss a leak event
  - Synthetic flow is automatically zeroed when the valve is off, simulating a true shutoff
  - All features are configurable via the integration's Options flow

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
- **Shutoff Valve Entity (optional)**

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
- **Auto shutoff on trigger (per-detector)**

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
- **The shutoff valve and auto-shutoff toggles can be changed at any time via Options.**

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
    - **auto_shutoff_on_trigger, auto_shutoff_effective, auto_shutoff_valve_entity, valve_off**
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
  - **Now also tracks the shutoff valve if configured**
- **Synthetic flow (number, optional)**
  - State: Current synthetic flow (gpm)
  - When the shutoff valve is off, this is automatically set to zero to simulate a true shutoff

## Shutoff Valve and Auto-Shutoff Details

- The shutoff valve can be any Home Assistant entity that supports on/off (switch, input_boolean, or valve)
- Each leak detector (low-flow, tank refill) can have auto-shutoff enabled or disabled independently
- When a leak is detected and auto-shutoff is enabled, the valve is turned off automatically
- While the valve is off, leak sensors will not clear, ensuring you don't miss a leak event
- Synthetic flow is automatically zeroed when the valve is off
- The Upstream Health sensor will show the valve as unavailable if it cannot be reached
- Leak sensor attributes:
  - **auto_shutoff_on_trigger**: True if auto-shutoff is enabled for this detector
  - **auto_shutoff_effective**: True if auto-shutoff is enabled and a valid valve is configured and available
  - **valve_off**: True if the valve is currently off

## How it works

...existing content from previous README...

## Changelog

### 0.4.0 (Unreleased)

- New: Shutoff valve support (switch/input_boolean/valve entity)
  - Per-detector auto-shutoff toggles
  - Leak sensors will not clear while valve is off
  - Synthetic flow is zeroed when valve is off
  - Upstream health sensor tracks valve
- Bugfix: Async event handler for valve state tracking
- Improved: Debug logging for valve state and synthetic flow

### 0.3.0

- New: Synthetic flow (gpm) test control as a Number entity
  - Options to include synthetic flow in detectors and/or the engine’s live calculations
  - Last/Current session sensors can include synthetic gallons when enabled, useful for simulation
- Engine behavior: Synthetic gallons are excluded from stored sessions and daily analysis (analyze_yesterday)
  - Daily totals and anomaly thresholds are computed without synthetic, so testing doesn’t skew analytics
  - Sessions that are purely synthetic are not recorded by the engine
- Stability: Improved session finalization logic and attributes for better visibility
- Session model: Removed the separate continuity window; a single Gap Tolerance governs within-session gaps and finalization. Session duration and averages now exclude gap time.
