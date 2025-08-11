# Prompt template: Add a new sensor to an existing Home Assistant integration without breaking config or reconfig

Copy/paste this prompt to instruct an AI to implement a new sensor for an existing integration while preserving the current ConfigFlow/OptionsFlow behavior, reconfiguration, and device/entity stability.

— START OF PROMPT TO AI —

You are updating an existing Home Assistant integration.

Context
- Integration name: {INTEGRATION_NAME}
- Domain: {DOMAIN}
- Repository path prefix: custom_components/{DOMAIN}/
- New entity to add: {NEW_SENSOR_NAME} ({NEW_SENSOR_KIND}: SensorEntity/BinarySensorEntity/etc.)
- Purpose of the new entity: {BRIEF_DESCRIPTION_OF_FEATURE}
- Upstream dependencies: {DEPENDENCY_ENTITIES_OR_NONE}
- User facing option to enable/disable: {ENABLE_OPTION_KEY} (boolean)
- Additional options: {LIST_OPTION_KEYS_WITH_TYPES_AND_DEFAULTS}

Non‑negotiable requirements
1) Backward compatibility
   - Do NOT change, remove, or rename existing unique_ids, CONF_* keys, or ConfigFlow/OptionsFlow step ids.
   - New options must be additive and optional, with safe defaults supplied via DEFAULTS in const.py.
   - Existing config entries must continue working without user interaction after upgrade.
   - If schema evolution is necessary, implement async_migrate_entry with a bumped entry version; otherwise avoid migrations.

2) Config loading and reconfiguration
   - Everywhere configuration is read, merge options over data:
     ex = {**entry.data, **entry.options}
   - Always fall back to DEFAULTS in const.py.
   - Ensure entry.add_update_listener is present in __init__.py and triggers await hass.config_entries.async_reload(entry.entry_id).
   - OptionsFlow must expose all new options. Never force the user through the initial ConfigFlow again for existing entries.

3) ConfigFlow and OptionsFlow integrity
   - Maintain the existing step order and ids; append a new step (or extend an existing “Advanced” step) for the new entity’s options.
   - Use Home Assistant selectors (e.g., entity, number, boolean) with proper defaults.
   - Guard the new sensor behind {ENABLE_OPTION_KEY}. If disabled or required dependencies are missing, skip creating the entity cleanly.

4) Entities and device grouping
   - All entities must share:
     DeviceInfo.identifiers = {(DOMAIN, entry.entry_id)}
     DeviceInfo.manufacturer = "{MANUFACTURER}"
     DeviceInfo.model = "{MODEL}"
   - DeviceInfo.name must be the configured sensor prefix (or entry.title), but respect device.name_by_user. Only set/adjust device name via device registry in __init__.py.

5) Stability and observability
   - Do not change existing entity_ids or unique_ids.
   - New entity unique_id format: f"{entry.entry_id}_{STABLE_SUFFIX}".
   - For binary sensors: never set unit_of_measurement; move units into attributes if needed.
   - For sensors: derive native_unit_of_measurement from the upstream source when applicable.

6) Runtime behavior
   - Subscribe to required upstream entities with async_track_state_change_event.
   - Use async_track_time_interval only when timing is required; unsubscribe when no longer needed.
   - Handle unknown/unavailable gracefully; avoid flapping availability.

Files to change or add
- custom_components/{DOMAIN}/const.py
  - Add CONF_* keys for {NEW_SENSOR_NAME} and DEFAULTS.
  - Keep names stable and additive; do not rename existing keys.

- custom_components/{DOMAIN}/config_flow.py
  - DO NOT modify ConfigFlow required step(s) unless strictly necessary.
  - Extend OptionsFlow:
    - Add/extend a step called "{STEP_ID_FOR_NEW_FEATURE}" with a schema containing:
      - {ENABLE_OPTION_KEY}: selector boolean (default from DEFAULTS)
      - other options: selectors with defaults
    - Ensure options are written to entry.options; do not mutate entry.data.
    - Keep step ordering stable and existing behavior intact.

- custom_components/{DOMAIN}/__init__.py
  - Ensure add_update_listener is set (reload on options changes).
  - Device registry sync:
    - identifiers={(DOMAIN, entry.entry_id)}
    - manufacturer="{MANUFACTURER}", model="{MODEL}"
    - device name = prefix or entry.title if not user-overridden (device.name_by_user respected)
  - Avoid migrations unless strictly needed; if needed, implement async_migrate_entry with version bump.

- custom_components/{DOMAIN}/sensor.py (or binary_sensor.py / platform file)
  - Implement {NEW_SENSOR_CLASS_NAME} with:
    - unique_id = f"{entry.entry_id}_{STABLE_SUFFIX}"
    - device_info as specified for grouping
    - Entity attributes and unit handling as appropriate
    - Setup gating: only create entity if {ENABLE_OPTION_KEY} is true AND dependencies exist
    - Robust state parsing (unknown/unavailable guards) and minimal periodic updates
  - Update async_setup_entry to instantiate the new entity conditionally.

- custom_components/{DOMAIN}/manifest.json
  - Bump version (patch/minor), no breaking changes.

Coding patterns to follow
- Read config uniformly:
  ex = {**entry.data, **entry.options}
  val = ex.get(CONF_KEY, DEFAULTS[CONF_KEY])
- Use clear, additive unique_id suffixes: e.g., “_{NEW_SENSOR_SUFFIX}”.
- DeviceInfo identical across all platform files for grouping.
- Avoid unit_of_measurement on BinarySensorEntity; expose “*_unit” attribute if needed.

Testing and acceptance criteria
- Fresh install:
  - Integration sets up; device groups all entities; device header shows prefix by {MANUFACTURER}, Entities table shows “{MODEL} by {MANUFACTURER}”.
  - New sensor can be enabled/disabled via Options; defaults do not create it unless enabled (if that’s the policy).

- Upgrade from previous version (existing entry):
  - No user intervention required; existing entities preserved (same unique_ids and entity_ids).
  - OptionsFlow shows new step/options; enabling creates the new entity after reload.

- Reconfigure:
  - Changing new options updates behavior after reload via update_listener.
  - Disabling the feature removes the entity cleanly (or marks it unavailable) without affecting others.

- Missing dependencies:
  - If upstream dependency is not configured, entity is not created (or is safely unavailable), with a log warning only.

- Code quality:
  - No references to removed/renamed CONF_* keys.
  - No changes to existing step ids or required fields in ConfigFlow.
  - New code paths are additive and well-guarded with defaults.

Deliverables
- Updated files in file blocks with exact paths:
  - const.py, config_flow.py, __init__.py, the relevant platform file(s), and manifest.json (version bump).
- Clear notes in comments referencing:
  - New CONF_* keys and DEFAULTS
  - Unique_id suffix used
  - The OptionsFlow step id added/extended
- A brief CHANGELOG or PR body summarizing the change and confirming no breaking changes.

Variables to use
- {INTEGRATION_NAME} = "<your integration name>"
- {DOMAIN} = "<your domain>"
- {NEW_SENSOR_NAME} = "<human friendly name>"
- {NEW_SENSOR_KIND} = "sensor|binary_sensor|..."
- {NEW_SENSOR_CLASS_NAME} = "<ClassName>"
- {NEW_SENSOR_SUFFIX} = "<stable_suffix>"
- {ENABLE_OPTION_KEY} = "CONF_<FEATURE>_ENABLE"
- {MANUFACTURER} = "markaggar"
- {MODEL} = "Water Session Tracking and Leak Detection"

— END OF PROMPT TO AI —

Notes for maintainers
- Keep the prompt with the repo to reuse for future additive sensors.
- If a breaking change is ever necessary, plan a migration strategy and announce it clearly; this template is for additive, non‑breaking changes.