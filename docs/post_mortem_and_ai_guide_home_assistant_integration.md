# Post‑mortem and guide: Instructing AI to produce a robust Home Assistant integration

This document captures lessons learned and a practical guide for instructing AI to build a working Home Assistant (HA) integration that:
- installs cleanly
- supports reconfiguration (OptionsFlow)
- has optional parameters and multiple configuration pages
- adds new entities without breaking existing configuration
- sets device metadata correctly (device name vs. model/manufacturer)
- keeps entities grouped under one device

It’s based on the real issues we encountered while iterating on the Water Monitor integration (sensor + binary_sensor).

---

## What went wrong (and why)

1) Device name vs. “Model by Manufacturer” confusion
- Symptom: Entities table showed “Low-flow Leak Detector by Water Monitor” even after renaming.
- Root cause: The Entities “Device” column displays Model by Manufacturer, not the device’s friendly name.
- Fix: Set DeviceInfo.model and DeviceInfo.manufacturer to the desired label for the table, and manage the device’s friendly name via the device registry (respecting user overrides).

2) Inconsistent device grouping
- Symptom: Some entities grouped under one device; others under another.
- Root cause: Entities used different DeviceInfo or different identifiers.
- Fix: Ensure all entities share the same DeviceInfo.identifiers = {(DOMAIN, entry.entry_id)} and consistent manufacturer/model. Keep grouping rules identical across platforms.

3) Reconfiguration did not fully propagate
- Symptom: Changing options did not update device metadata or entity behavior until a restart.
- Root cause: Missing entry.add_update_listener and/or no device registry sync in async_setup_entry.
- Fix: Add an update listener to reload platforms on options changes; use device registry to update manufacturer/model/name one time on setup.

4) Optional parameters and multiple pages
- Symptom: Options appeared but logic didn’t handle missing or optional values gracefully.
- Root cause: Inconsistent reads from entry.data vs entry.options, missing defaults, and lack of schema-driven gating.
- Fix: Always merge options over data, define defaults in const.py, and gate features with explicit enables.

5) Adding a new entity risks breaking existing config
- Symptom: New entity might change device or entity metadata in a way that un-groups or renames existing items.
- Root cause: Divergent DeviceInfo and unique_id schemes; changing existing unique_ids.
- Fix: Never change existing unique_ids; ensure new entities use the same identifiers and conventions; prefer additive evolution.

---

## Principles to enforce in prompts to AI

- Configuration
  - “Generate a full ConfigFlow + OptionsFlow with multiple steps (pages). Use voluptuous and selectors.”
  - “All options are reconfigurable. Use entry.options for runtime, fall back to entry.data + DEFAULTS.”
  - “Add entry.add_update_listener to reload platforms when options change.”
  - “Do not store derived/transient values in the config entry.”

- Entities and Devices
  - “All entities share a single device via DeviceInfo.identifiers={(DOMAIN, entry.entry_id)}.”
  - “Set DeviceInfo.manufacturer and model to exactly the strings we want visible in the Entities ‘Device’ column.”
  - “Set the device’s friendly name to the sensor prefix (entry title or configured prefix) via device registry in async_setup_entry; respect name_by_user.”

- Backward compatibility
  - “Never change unique_id once shipped. New entities must have suffix-based unique_ids derived from entry.entry_id.”
  - “If schema changes, implement async_migrate_entry with versioning.”

- Optional features
  - “Guard optional features with explicit enable flags (e.g., ‘enable_low_flow’). If disabled or missing upstream sensor, skip entity creation cleanly.”

- Units and attributes
  - “Binary sensors never expose unit_of_measurement; use a separate attribute like flow_unit.”
  - “For sensors, set native unit dynamically from the source entity.”

- Performance and update strategy
  - “Subscribe to upstream entity state changes and add time-based updates only when necessary.”
  - “Use callbacks to share state between related entities to avoid duplicate computation.”

- User overrides
  - “When updating device name in the registry, keep device.name if device.name_by_user is set.”

---

## Configuration patterns that work

- Constants and defaults
  - Put all CONF_* keys and DEFAULTS in const.py.
  - Example read pattern: ex = {**entry.data, **entry.options}; value = ex.get(KEY, DEFAULTS[KEY])

- ConfigFlow and OptionsFlow
  - ConfigFlow collects minimum viable info; OptionsFlow handles feature toggles and optional extras.
  - Use multiple Steps (pages) with schema sections:
    - Step 1: Required sensors
    - Step 2: Session settings
    - Step 3: Optional leak detection
    - Step 4: Advanced/tuning

- Reconfiguration
  - Add listener: entry.async_on_unload(entry.add_update_listener(_update_listener))
  - In listener: await hass.config_entries.async_reload(entry.entry_id)

- Device registry fix‑up
  - In async_setup_entry of __init__.py:
    - Get or create device with identifiers={(DOMAIN, entry.entry_id)}
    - Compute prefix = options.get(CONF_SENSOR_PREFIX) or entry.title or "Water Monitor"
    - desired_name = device.name if device.name_by_user else prefix
    - Update manufacturer="markaggar" and model="Water Session Tracking and Leak Detection"
    - dev_reg.async_update_device(...)

---

## Device and Entity registry do’s and don’ts

- Do
  - Share the same identifiers for all entities to group them under a single device.
  - Set model/manufacturer consistently so the Entities table shows “Model by Manufacturer” as intended.
  - Respect name_by_user and only set device name when not user‑overridden.

- Don’t
  - Don’t hardcode different model/manufacturer across platforms or files.
  - Don’t change existing unique_ids or you’ll duplicate or orphan entities.
  - Don’t insert unit_of_measurement on binary sensors.

---

## Adding a new sensor safely

- unique_id
  - Use f"{entry.entry_id}_{stable_suffix}". Never change for the lifetime of that entity.

- DeviceInfo
  - Keep identifiers and manufacturer/model identical to existing entities.
  - Derive device name from prefix but only set via registry once (respect user override).

- Options gating
  - Put the feature behind an enable flag, and skip entity creation if missing dependencies.

- Unit synchronization
  - For derived sensors, copy units from the source sensor’s attributes.

- Testing checklist
  - Fresh install: integration sets up, entities group correctly, device name uses prefix.
  - Reconfigure options: entities reload; device model/manufacturer consistent.
  - Add new entity: no duplication; existing entity_ids stable.
  - Remove optional upstream sensor: optional entity not created; others unaffected.

---

## Example reading of configuration everywhere

```python
# Always merge options over data and fall back to defaults
ex = {**entry.data, **entry.options}
prefix = ex.get(CONF_SENSOR_PREFIX, DEFAULTS[CONF_SENSOR_PREFIX])
flow = ex.get(CONF_FLOW_SENSOR, "")
hot = ex.get(CONF_HOT_WATER_SENSOR)  # optional
```

---

## Example DeviceInfo consistency

```python
from homeassistant.helpers.device_registry import DeviceInfo
from .const import DOMAIN, CONF_SENSOR_PREFIX

@property
def device_info(self) -> DeviceInfo:
    ex = {**self._entry.data, **self._entry.options}
    prefix = ex.get(CONF_SENSOR_PREFIX) or self._entry.title or "Water Monitor"
    return DeviceInfo(
        identifiers={(DOMAIN, self._entry.entry_id)},
        name=prefix,  # friendly device name (also enforced once via registry)
        manufacturer="markaggar",
        model="Water Session Tracking and Leak Detection",
    )
```

---

## Prompt template to give the AI

Use this as a starting point when you ask an AI to build or modify a HA integration:

- Create a Home Assistant integration named X with domain X.
- Include: manifest.json, __init__.py, config_flow.py (with OptionsFlow), const.py, sensor.py, binary_sensor.py, and a tracker/helper module.
- ConfigFlow gathers required sensors; OptionsFlow exposes optional features and advanced tuning. Use multiple steps (pages).
- Store initial data in entry.data; runtime values come from entry.options merged over entry.data with DEFAULTS from const.py.
- Add entry.add_update_listener to reload platforms on options changes.
- All entities share one device using DeviceInfo.identifiers={(DOMAIN, entry.entry_id)}.
- Set DeviceInfo.manufacturer="markaggar", model="Water Session Tracking and Leak Detection". Device name must be the configured prefix or entry title. Respect name_by_user.
- Never change existing unique_ids. New entities use f"{entry.entry_id}_{suffix}".
- Binary sensors must not set unit_of_measurement; put units in a separate attribute.
- Derive sensor units dynamically from source entities. Use callbacks to sync state between related sensors.
- Subscribe to upstream entities and use async_track_time_interval only when needed (e.g., continuation windows).
- Include async_migrate_entry if schema changes; version your entries.

---

## Final checklist before release

- [ ] Fresh install works, entities grouped, device header correct.
- [ ] Options changes reload integration; behavior updates without restart.
- [ ] Optional features gate correctly and skip cleanly if dependencies missing.
- [ ] Entity unique_ids are stable and documented.
- [ ] DeviceInfo (identifiers/manufacturer/model) consistent across all platforms.
- [ ] Device name uses prefix; user override respected.
- [ ] Units correct; binary sensors don’t expose unit_of_measurement.
- [ ] Brands icons prepared (monochrome, currentColor).