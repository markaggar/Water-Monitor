from __future__ import annotations

DOMAIN = "water_monitor"

# Existing config keys
CONF_FLOW_SENSOR = "flow_sensor"
CONF_VOLUME_SENSOR = "volume_sensor"
CONF_HOT_WATER_SENSOR = "hot_water_sensor"
CONF_MIN_SESSION_VOLUME = "min_session_volume"
CONF_MIN_SESSION_DURATION = "min_session_duration"
CONF_SESSION_GAP_TOLERANCE = "session_gap_tolerance"
CONF_SESSION_CONTINUITY_WINDOW = "session_continuity_window"
CONF_SENSOR_PREFIX = "sensor_prefix"

# Update cadence for periodic evaluators
UPDATE_INTERVAL = 1  # seconds

# Low-flow leak detector (optional) - config/option keys
CONF_LOW_FLOW_ENABLE = "low_flow_enable"
CONF_LOW_FLOW_MAX_FLOW = "low_flow_max_flow"  # threshold defining "low flow" (e.g., 0.5 gpm)
CONF_LOW_FLOW_SEED_S = "low_flow_seed_low_flow_duration_s"  # continuous low-flow time needed to seed detection
CONF_LOW_FLOW_MIN_S = "low_flow_min_duration_s"  # persistence time to trigger leak
CONF_LOW_FLOW_CLEAR_IDLE_S = "low_flow_clear_idle_s"  # zero-flow time to clear/reset
CONF_LOW_FLOW_COUNTING_MODE = "low_flow_counting_mode"  # nonzero_wallclock | in_range_only
CONF_LOW_FLOW_SMOOTHING_S = "low_flow_smoothing_window_s"
CONF_LOW_FLOW_COOLDOWN_S = "low_flow_cooldown_s"
CONF_LOW_FLOW_CLEAR_ON_HIGH_S = "low_flow_clear_on_sustained_high_flow_s"  # optional, can be None (disabled)

COUNTING_MODE_NONZERO = "nonzero_wallclock"
COUNTING_MODE_IN_RANGE = "in_range_only"

# Defaults
DEFAULTS = {
    CONF_MIN_SESSION_VOLUME: 0.0,
    CONF_MIN_SESSION_DURATION: 0,
    CONF_SESSION_GAP_TOLERANCE: 5,
    CONF_SESSION_CONTINUITY_WINDOW: 3,
    CONF_SENSOR_PREFIX: "Water Monitor",
    # Low-flow
    CONF_LOW_FLOW_ENABLE: False,
    CONF_LOW_FLOW_MAX_FLOW: 0.5,
    CONF_LOW_FLOW_SEED_S: 60,
    CONF_LOW_FLOW_MIN_S: 300,
    CONF_LOW_FLOW_CLEAR_IDLE_S: 30,
    CONF_LOW_FLOW_COUNTING_MODE: COUNTING_MODE_NONZERO,
    CONF_LOW_FLOW_SMOOTHING_S: 5,
    CONF_LOW_FLOW_COOLDOWN_S: 0,
    CONF_LOW_FLOW_CLEAR_ON_HIGH_S: None,
}