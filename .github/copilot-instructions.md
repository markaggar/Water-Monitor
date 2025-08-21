# Copilot Instructions: Water Monitor Integration

Purpose: Enable AI agents to quickly and safely enhance the Home Assistant custom integration `water_monitor`.

## Architecture Overview
- Domain: `custom_components/water_monitor/` implements a stateful engine plus several HA platforms (binary_sensor, sensor, number) and configuration flows.
- Core engine logic lives in `__init__.py` and `water_session_tracker.py` (session detection, leak tracking, dispatch of updates via `dispatcher` signals).
- Platforms:
  - `sensor.py`: Exposes session-derived metrics (last/current session volume, duration, average flow, hot water %).
  - `binary_sensor.py`: Upstream health, low-flow leak, tank refill leak, intelligent leak, engine status.
  - `number` (synthetic flow) created dynamically inside `__init__.py` when synthetic testing enabled.
- Config & Options flows: `config_flow.py` drives multi-step forms for base setup, leak detectors, intelligent leak detection, and shutoff valve.
- Constants & signal helpers: `const.py` centralizes all option keys and provides `engine_signal(entry_id)` and `tracker_signal(entry_id)` for dispatcher-based decoupling.
- Event / data flow:
  1. HA state changes (flow, volume, hot water, valve, synthetic number) are observed.
  2. Engine/session tracker updates internal state and emits dispatcher signals.
  3. Entities subscribe to tracker/engine signals to refresh attributes and state (`async_write_ha_state`).
  4. Leak detectors optionally trigger auto-shutoff (valve off) and suppression of clearing while valve is off.

## Key Patterns & Conventions
- Unique IDs: Prefixed with `entry.entry_id` plus semantic suffix (e.g., `_low_flow_leak`, `_last_session_volume`). When creating new entities, follow this pattern for multi-instance safety.
- Dispatcher signals: Never call entities directly—emit via `engine_signal(entry_id)` / `tracker_signal(entry_id)` and let subscribers react.
- Async rules: All I/O and HA interactions are `async`. Event handlers must be `async` or decorated callbacks; avoid blocking calls or `time.sleep`.
- Optional sensors: Volume, hot water, valve are optional and normalized to `None` if unset or empty; logic must gracefully degrade (e.g., health sensor treats `None` as not-required OK).
- Synthetic flow: When valve is OFF, synthetic flow number is forcibly set to 0 to simulate a hard shutoff. Don’t persist synthetic volume in historical/daily metrics—engine excludes it.
- Leak auto-shutoff: Per-detector flags (`auto_shutoff_on_trigger` vs `auto_shutoff_effective`). Only trigger valve off when effective = True.
- Health sensor: `UpstreamHealthBinarySensor` only gates overall connectivity on required sensors (flow, optional volume, optional hot water); valve presence does not cause failure if missing.
- Attribute transparency: Most binary sensors expose timers/progress counters; preserve naming; add new fields only when they’re stable.

## Adding Features Safely
1. Identify if new behavior belongs in engine logic (`__init__.py` / tracker) or as a derived entity.
2. Extend constants in `const.py` (add config keys) and include in `config_flow.py` (initial form + options flow).
3. Propagate new runtime values through dispatcher payloads instead of direct cross-entity access.
4. Ensure multi-instance isolation by scoping all shared data under `hass.data[DOMAIN][entry_id]` and using unique IDs with entry prefix.
5. Maintain backward compatibility: don’t rename existing unique_ids or entity names without migration.

## Testing & Dev Workflow
- Rapid iteration: Use `scripts/deploy-water-monitor.ps1` (and optional git pre-push hook) to copy the component to a HA instance and restart/core check automatically.
- Simulation utilities in `scripts/` let you inject synthetic usage or leak patterns; set required env vars (`HA_BASE_URL`, `HA_TOKEN`).
- Validation: PowerShell scripts (`validate-low-flow-shutoff.ps1`, `check-valve-attrs.ps1`) exercise leak + valve flows.
- Enable debug logging:
  ```yaml
  logger:
    logs:
      custom_components.water_monitor: debug
  ```

## Common Pitfalls
- Forgetting to normalize optional entity IDs -> leads to false negatives in health sensor.
- Blocking code in callbacks -> starves HA event loop.
- Writing directly to synthetic flow while valve is OFF -> engine will overwrite to 0; ensure desired effect after valve ON.
- Misusing auto-shutoff flags: only act when both configuration flag and valve presence yield `auto_shutoff_effective`.

## Examples
- Emit tracker update:
  ```python
  async_dispatcher_send(hass, tracker_signal(entry_id), tracker_state_dict)
  ```
- Create new binary sensor unique ID:
  ```python
  self._attr_unique_id = f"{entry.entry_id}_my_feature_flag"
  ```
- Access engine instance:
  ```python
  engine = hass.data[DOMAIN][entry.entry_id]["engine"]
  ```

## Style & Quality
- Keep functions short and side-effect explicit; prefer pure computations in tracker/engine and thin HA entity layers.
- Use defensive `try/except` inside callbacks to avoid breaking dispatcher chains.
- Avoid global mutable state—always key by `entry_id`.

## When Unsure
- Search `const.py` for naming precedents.
- Mirror existing leak sensor patterns for new detectors (state machine + timers + attributes + periodic tick).
- Ask for clarification if a behavior overlaps engine vs entity responsibilities.

(End)
