# Water Monitor architecture overview

This document outlines the architecture of the Water Monitor integration with emphasis on how the sensor files connect and share state.

Integration goals
- Track discrete water “sessions” from flow and volume sources.
- Report the last completed session and the current/in-progress session volume.
- Detect sustained low‑flow leaks as a binary sensor.
- Keep all entities grouped under one device and present consistent branding.

---

## Key modules and responsibilities

- const.py
  - DOMAIN and all CONF_* keys
  - DEFAULTS for all options
  - UPDATE_INTERVAL for periodic checks

- __init__.py
  - async_setup_entry: forwards to platforms ["sensor", "binary_sensor"]
  - One-time device registry sync:
    - identifiers={(DOMAIN, entry.entry_id)} (stable grouping)
    - manufacturer="markaggar"
    - model="Water Session Tracking and Leak Detection"
    - device name set to prefix unless user has renamed the device
  - add_update_listener to reload on options changes

- water_session_tracker.py
  - Stateless (or minimal state) engine that ingests:
    - flow_rate (instantaneous)
    - volume_total (cumulative)
    - hot_water_active (optional)
    - timestamp
  - Emits structured session state:
    - current_session_* (active session metrics)
    - intermediate_session_* (gap window/continuation)
    - last_session_* (finalized session metrics)
    - flags like gap_active, current_session_active
  - Provides derived fields (averages, durations)

- sensor.py
  - WaterSessionSensor (main)
    - Entity: reports last completed session volume (rounded)
    - Reads upstream flow/volume/hot_water entities from HA state machine
    - Initializes and advances WaterSessionTracker
    - Derives unit from the source volume sensor
    - Decides when periodic updates are necessary (gap/session continuation)
    - Publishes full state dict as attributes
    - Triggers a callback into the “current” sensor
    - DeviceInfo is consistent with binary_sensor.py to maintain grouping

  - CurrentSessionVolumeSensor (secondary)
    - Entity: reports current or intermediate session volume (rounded)
    - Receives state via callback from WaterSessionSensor (no duplicate processing)
    - Triages whether to surface current, intermediate, or final metrics
    - Mirrors the volume unit passed in state_data

- binary_sensor.py
  - LowFlowLeakBinarySensor
    - Entity: BinarySensorDeviceClass.PROBLEM
    - Smoothing buffer over latest flow readings (time-weighted)
    - State machine:
      - idle → seeding → counting → triggered
      - Clear on sustained zero or optional sustained high-flow
      - Optional cooldown to avoid flapping
    - Attributes expose tuning and debug state
    - No unit_of_measurement (binary sensor), but “flow_unit” attribute for clarity
    - DeviceInfo matches the sensors for grouping and branding

---

## Configuration and options

- Config is split between entry.data (initial) and entry.options (runtime).
- Read pattern everywhere:
  - ex = {**entry.data, **entry.options}
  - ex.get(CONF_X, DEFAULTS[CONF_X]) as needed

- OptionsFlow provides:
  - Required upstream sensors (flow, volume)
  - Optional hot water sensor
  - Session detection thresholds
  - Low-flow leak detection enable + parameters
  - Advanced tuning (smoothing, timers)

- __init__.py enforces device registry metadata and reloads platforms when options change.

---

## Entity relationships and data flow

1) Upstream sources (user-provided in Options):
- flow sensor: numeric instantaneous flow rate
- volume sensor: numeric cumulative volume
- hot water sensor: optional boolean

2) WaterSessionSensor (main)
- Subscribes to flow/volume[/hot] state changes
- On events or periodic tick:
  - Reads HA states (guard unknown/unavailable)
  - Parses numeric values
  - Feeds WaterSessionTracker
  - Updates its own state: last_session_volume
  - Stores tracker output in attributes
  - Forwards a compact state_data dict to CurrentSessionVolumeSensor via callback
  - Schedules periodic updates if needed (gap continuation, etc.)

3) CurrentSessionVolumeSensor (secondary)
- Receives state_data from the main sensor
- Chooses stage (“current”, “intermediate”, “final”) to compute displayed volume
- Mirrors units and publishes triaged attributes

4) LowFlowLeakBinarySensor
- Subscribes to flow sensor and also runs periodic ticks
- Maintains a smoothing buffer and a state machine
- Sets is_on with clear conditions and optional cooldown
- Exposes tuning timings and last_triggered in attributes

5) Device grouping and branding
- All entities share:
  - identifiers={(DOMAIN, entry.entry_id)}
  - manufacturer="markaggar"
  - model="Water Session Tracking and Leak Detection"
- Device name is the configured prefix (or entry.title). If user renamed the device, that override is preserved.

---

## Update scheduling

- WaterSessionSensor
  - Event-driven on upstream changes
  - Uses async_track_time_interval only when necessary:
    - Gap monitoring
    - Session continuation window after flow drops to zero
  - Cancels periodic ticks when not needed

- CurrentSessionVolumeSensor
  - Callback-driven only, no poll

- LowFlowLeakBinarySensor
  - Event-driven on flow changes
  - Always maintains periodic ticks to advance timers/state machine

---

## Units and presentation

- Volume unit is derived from the upstream volume sensor and mirrored by both sensors.
- Binary sensor never sets unit_of_measurement; it exposes an attribute “flow_unit”.
- Sensors round displayed volumes (e.g., to 2 decimals) but keep raw values in attributes for transparency.

---

## Error handling and availability

- Guard “unknown”/“unavailable” states before parsing.
- Try/except around numeric conversions.
- Keep entities available unless upstream becomes unavailable for extended periods; avoid flapping.

---

## File map and connections

- custom_components/water_monitor/__init__.py
  - Entry point, device registry sync, reload listener

- custom_components/water_monitor/const.py
  - DOMAIN, CONF_*, DEFAULTS, UPDATE_INTERVAL

- custom_components/water_monitor/water_session_tracker.py
  - Session detection engine

- custom_components/water_monitor/sensor.py
  - WaterSessionSensor (main)
  - CurrentSessionVolumeSensor (secondary)
  - Callback from main → current

- custom_components/water_monitor/binary_sensor.py
  - LowFlowLeakBinarySensor (low‑flow leak state machine)

All three entities’ device_info match to ensure grouping and consistent branding.

---

## Extensibility notes

- Adding a new metric sensor:
  - unique_id = f"{entry.entry_id}_{new_suffix}"
  - DeviceInfo same as others
  - Subscribe to same upstream sources or reuse tracker state via a shared coordinator/callback
  - Gate behind an “enable” option and dependency checks

- Schema evolution:
  - Bump config entry version and implement async_migrate_entry; keep unique_ids stable.

- UI branding:
  - Submit icon.svg and logo.svg to home-assistant/brands under custom_integrations/water_monitor/ using currentColor, single-path SVGs.

---

## Sequence example (session)

1) Flow rises above zero → WaterSessionSensor reads states, updates tracker.
2) Tracker starts a session; CurrentSessionVolumeSensor begins reporting current_session_volume.
3) Flow stops → tracker enters gap/continuation window; WaterSessionSensor schedules periodic ticks.
4) Gap expires → tracker finalizes last_session_*; periodic ticks cancel.
5) Current sensor drops to 0, last session metrics stay in attributes.

This decouples “current display” from “last finalized” while avoiding duplicated computations.
