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

# Session boundary behavior
CONF_SESSIONS_USE_BASELINE_AS_ZERO = "sessions_use_baseline_as_zero"
CONF_SESSIONS_IDLE_TO_CLOSE_S = "sessions_idle_to_close_s"

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
COUNTING_MODE_BASELINE_LATCH = "baseline_latch"

# Low-flow baseline latch options
CONF_LOW_FLOW_BASELINE_MARGIN_PCT = "low_flow_baseline_margin_pct"


# Defaults
DEFAULTS = {
    CONF_MIN_SESSION_VOLUME: 0.0,
    CONF_MIN_SESSION_DURATION: 0,
    CONF_SESSION_GAP_TOLERANCE: 5,
    CONF_SESSION_CONTINUITY_WINDOW: 3,
    CONF_SENSOR_PREFIX: "Water Monitor",
    # Session boundary behavior
    CONF_SESSIONS_USE_BASELINE_AS_ZERO: True,
    CONF_SESSIONS_IDLE_TO_CLOSE_S: 10,
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
    CONF_LOW_FLOW_BASELINE_MARGIN_PCT: 10.0,
    # Tank refill leak (disabled by default)
    # Enable flag
    # Thresholds and behavior
    # Note: volumes use the same unit as the configured volume sensor
}

# Tank refill leak detector (optional) - config/option keys
CONF_TANK_LEAK_ENABLE = "tank_refill_leak_enable"
CONF_TANK_LEAK_MIN_REFILL_VOLUME = "tank_refill_min_volume"  # minimum session volume to be considered a refill
CONF_TANK_LEAK_MAX_REFILL_VOLUME = "tank_refill_max_volume"  # optional: ignore events above this volume (0 disables)
CONF_TANK_LEAK_TOLERANCE_PCT = "tank_refill_volume_tolerance_pct"  # percent similarity window
CONF_TANK_LEAK_REPEAT_COUNT = "tank_refill_repeat_count"  # consecutive similar refills needed to trigger
CONF_TANK_LEAK_WINDOW_S = "tank_refill_window_s"  # time window to count repeats
CONF_TANK_LEAK_CLEAR_IDLE_S = "tank_refill_clear_idle_s"  # time with no matching refills to auto-clear
CONF_TANK_LEAK_COOLDOWN_S = "tank_refill_cooldown_s"  # suppress re-triggering after clear
CONF_TANK_LEAK_MIN_REFILL_DURATION_S = "tank_refill_min_duration_s"  # optional: ignore events shorter than this (0 disables)
CONF_TANK_LEAK_MAX_REFILL_DURATION_S = "tank_refill_max_duration_s"  # optional: ignore events longer than this (0 disables)

# Extend defaults after keys are declared
DEFAULTS.update({
    CONF_TANK_LEAK_ENABLE: False,
    CONF_TANK_LEAK_MIN_REFILL_VOLUME: 0.3,
    CONF_TANK_LEAK_MAX_REFILL_VOLUME: 0.0,  # 0 = disabled
    CONF_TANK_LEAK_TOLERANCE_PCT: 10.0,
    CONF_TANK_LEAK_REPEAT_COUNT: 3,
    CONF_TANK_LEAK_WINDOW_S: 15 * 60,  # 15 minutes
    CONF_TANK_LEAK_CLEAR_IDLE_S: 30 * 60,  # 30 minutes of no matching refills
    CONF_TANK_LEAK_COOLDOWN_S: 0,
    CONF_TANK_LEAK_MIN_REFILL_DURATION_S: 0,
    CONF_TANK_LEAK_MAX_REFILL_DURATION_S: 0,
})