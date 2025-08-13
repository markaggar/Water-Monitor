from __future__ import annotations

import voluptuous as vol
from typing import Any, Dict, Optional

from homeassistant import config_entries
from homeassistant.core import callback

from .const import (
    DOMAIN,
    # base
    CONF_FLOW_SENSOR,
    CONF_VOLUME_SENSOR,
    CONF_HOT_WATER_SENSOR,
    CONF_MIN_SESSION_VOLUME,
    CONF_MIN_SESSION_DURATION,
    CONF_SESSION_GAP_TOLERANCE,
    CONF_SESSION_CONTINUITY_WINDOW,
    CONF_SENSOR_PREFIX,
    CONF_SESSIONS_USE_BASELINE_AS_ZERO,
    CONF_SESSIONS_IDLE_TO_CLOSE_S,
    # occupancy / intelligent
    CONF_OCC_MODE_ENTITY,
    CONF_OCC_STATE_AWAY,
    CONF_OCC_STATE_VACATION,
    CONF_INTEL_DETECT_ENABLE,
    CONF_INTEL_LEARNING_ENABLE,
    DEFAULTS,
    # low-flow
    CONF_LOW_FLOW_ENABLE,
    CONF_LOW_FLOW_MAX_FLOW,
    CONF_LOW_FLOW_SEED_S,
    CONF_LOW_FLOW_MIN_S,
    CONF_LOW_FLOW_CLEAR_IDLE_S,
    CONF_LOW_FLOW_COUNTING_MODE,
    CONF_LOW_FLOW_SMOOTHING_S,
    CONF_LOW_FLOW_COOLDOWN_S,
    CONF_LOW_FLOW_CLEAR_ON_HIGH_S,
    COUNTING_MODE_NONZERO,
    COUNTING_MODE_IN_RANGE,
    COUNTING_MODE_BASELINE_LATCH,
    CONF_LOW_FLOW_BASELINE_MARGIN_PCT,
    # tank refill leak
    CONF_TANK_LEAK_ENABLE,
    CONF_TANK_LEAK_MIN_REFILL_VOLUME,
    CONF_TANK_LEAK_MAX_REFILL_VOLUME,
    CONF_TANK_LEAK_TOLERANCE_PCT,
    CONF_TANK_LEAK_REPEAT_COUNT,
    CONF_TANK_LEAK_WINDOW_S,
    CONF_TANK_LEAK_CLEAR_IDLE_S,
    CONF_TANK_LEAK_COOLDOWN_S,
    CONF_TANK_LEAK_MIN_REFILL_DURATION_S,
    CONF_TANK_LEAK_MAX_REFILL_DURATION_S,
)

# Try to use HA selectors; fall back to plain types if not available
HAS_SELECTORS = True
try:
    from homeassistant.helpers.selector import selector as ha_selector
except Exception:
    HAS_SELECTORS = False
    ha_selector = None  # type: ignore


def s_entity(domain: str):
    if HAS_SELECTORS:
        return ha_selector({"entity": {"domain": domain}})
    return str  # fallback: free text entity_id


def s_number(min_: float | int = 0, step: float | int = 1, mode: str = "box"):
    if HAS_SELECTORS:
        return ha_selector({"number": {"min": min_, "step": step, "mode": mode}})
    return vol.Coerce(float)


def s_int(min_: int = 0, step: int = 1, mode: str = "box"):
    if HAS_SELECTORS:
        return ha_selector({"number": {"min": min_, "step": step, "mode": mode}})
    return vol.Coerce(int)


def s_bool():
    if HAS_SELECTORS:
        return ha_selector({"boolean": {}})
    return vol.Coerce(bool)


def s_select(options: list[str]):
    if HAS_SELECTORS:
        return ha_selector({"select": {"options": options}})
    return vol.In(options)


def s_text():
    if HAS_SELECTORS:
        return ha_selector({"text": {}})
    return str


def _clean_optional_seconds(value: Any) -> Optional[int]:
    """Convert possibly blank/None to int seconds or None."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip()
    if s == "":
        return None
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return None


def _main_schema(existing: Optional[Dict[str, Any]] = None) -> vol.Schema:
    ex = existing or {}
    fields: Dict[Any, Any] = {}

    fields[vol.Required(CONF_SENSOR_PREFIX, default=ex.get(CONF_SENSOR_PREFIX, DEFAULTS[CONF_SENSOR_PREFIX]))] = str
    fields[vol.Required(CONF_FLOW_SENSOR, default=ex.get(CONF_FLOW_SENSOR, ""))] = s_entity("sensor")
    fields[vol.Required(CONF_VOLUME_SENSOR, default=ex.get(CONF_VOLUME_SENSOR, ""))] = s_entity("sensor")

    # Optional hot water sensor; include default if present so reconfigure shows prior value
    existing_hot = ex.get(CONF_HOT_WATER_SENSOR, None)
    if existing_hot in (None, ""):
        fields[vol.Optional(CONF_HOT_WATER_SENSOR)] = s_entity("binary_sensor")
    else:
        fields[vol.Optional(CONF_HOT_WATER_SENSOR, default=existing_hot)] = s_entity("binary_sensor")

    fields[vol.Required(CONF_MIN_SESSION_VOLUME, default=ex.get(CONF_MIN_SESSION_VOLUME, DEFAULTS[CONF_MIN_SESSION_VOLUME]))] = s_number(
        min_=0, step=0.01
    )
    fields[vol.Required(CONF_MIN_SESSION_DURATION, default=ex.get(CONF_MIN_SESSION_DURATION, DEFAULTS[CONF_MIN_SESSION_DURATION]))] = s_int(
        min_=0, step=1
    )
    fields[vol.Required(CONF_SESSION_GAP_TOLERANCE, default=ex.get(CONF_SESSION_GAP_TOLERANCE, DEFAULTS[CONF_SESSION_GAP_TOLERANCE]))] = s_int(
        min_=0, step=1
    )
    fields[vol.Required(CONF_SESSION_CONTINUITY_WINDOW, default=ex.get(CONF_SESSION_CONTINUITY_WINDOW, DEFAULTS[CONF_SESSION_CONTINUITY_WINDOW]))] = s_int(
        min_=0, step=1
    )
    # Session boundary behavior
    fields[vol.Required(CONF_SESSIONS_USE_BASELINE_AS_ZERO, default=ex.get(CONF_SESSIONS_USE_BASELINE_AS_ZERO, DEFAULTS[CONF_SESSIONS_USE_BASELINE_AS_ZERO]))] = s_bool()
    fields[vol.Required(CONF_SESSIONS_IDLE_TO_CLOSE_S, default=ex.get(CONF_SESSIONS_IDLE_TO_CLOSE_S, DEFAULTS[CONF_SESSIONS_IDLE_TO_CLOSE_S]))] = s_int(min_=0, step=1)

    # Experimental intelligent detection: separate page toggle
    fields[vol.Required(CONF_INTEL_DETECT_ENABLE, default=ex.get(CONF_INTEL_DETECT_ENABLE, DEFAULTS[CONF_INTEL_DETECT_ENABLE]))] = s_bool()

    fields[vol.Required(CONF_LOW_FLOW_ENABLE, default=ex.get(CONF_LOW_FLOW_ENABLE, DEFAULTS[CONF_LOW_FLOW_ENABLE]))] = s_bool()
    fields[vol.Required(CONF_TANK_LEAK_ENABLE, default=ex.get(CONF_TANK_LEAK_ENABLE, DEFAULTS[CONF_TANK_LEAK_ENABLE]))] = s_bool()

    return vol.Schema(fields)


def _low_flow_schema(existing: Optional[Dict[str, Any]] = None) -> vol.Schema:
    ex = existing or {}
    fields: Dict[Any, Any] = {}

    fields[vol.Required(CONF_LOW_FLOW_MAX_FLOW, default=ex.get(CONF_LOW_FLOW_MAX_FLOW, DEFAULTS[CONF_LOW_FLOW_MAX_FLOW]))] = s_number(
        min_=0.01, step=0.01
    )
    fields[vol.Required(CONF_LOW_FLOW_SEED_S, default=ex.get(CONF_LOW_FLOW_SEED_S, DEFAULTS[CONF_LOW_FLOW_SEED_S]))] = s_int(
        min_=0, step=1
    )
    fields[vol.Required(CONF_LOW_FLOW_MIN_S, default=ex.get(CONF_LOW_FLOW_MIN_S, DEFAULTS[CONF_LOW_FLOW_MIN_S]))] = s_int(
        min_=1, step=1
    )
    fields[vol.Required(CONF_LOW_FLOW_CLEAR_IDLE_S, default=ex.get(CONF_LOW_FLOW_CLEAR_IDLE_S, DEFAULTS[CONF_LOW_FLOW_CLEAR_IDLE_S]))] = s_int(
        min_=1, step=1
    )

    # Counting mode: safe, labeled options
    if HAS_SELECTORS:
        fields[vol.Required(
            CONF_LOW_FLOW_COUNTING_MODE,
            default=ex.get(CONF_LOW_FLOW_COUNTING_MODE, DEFAULTS[CONF_LOW_FLOW_COUNTING_MODE])
        )] = ha_selector({
            "select": {
                "options": [
                    {"label": "Any non-zero flow (wall clock)", "value": COUNTING_MODE_NONZERO},
                    {"label": "Only time within low-flow range", "value": COUNTING_MODE_IN_RANGE},
                    {"label": "Baseline latch (with optional expected baseline)", "value": COUNTING_MODE_BASELINE_LATCH}
                ],
                "mode": "list"
            }
        })
    else:
        fields[vol.Required(
            CONF_LOW_FLOW_COUNTING_MODE,
            default=ex.get(CONF_LOW_FLOW_COUNTING_MODE, DEFAULTS[CONF_LOW_FLOW_COUNTING_MODE])
        )] = vol.In([COUNTING_MODE_NONZERO, COUNTING_MODE_IN_RANGE])

    fields[vol.Required(CONF_LOW_FLOW_SMOOTHING_S, default=ex.get(CONF_LOW_FLOW_SMOOTHING_S, DEFAULTS[CONF_LOW_FLOW_SMOOTHING_S]))] = s_int(
        min_=0, step=1
    )
    fields[vol.Required(CONF_LOW_FLOW_BASELINE_MARGIN_PCT, default=ex.get(CONF_LOW_FLOW_BASELINE_MARGIN_PCT, DEFAULTS[CONF_LOW_FLOW_BASELINE_MARGIN_PCT]))] = s_number(min_=0, step=0.5)
    fields[vol.Required(CONF_LOW_FLOW_COOLDOWN_S, default=ex.get(CONF_LOW_FLOW_COOLDOWN_S, DEFAULTS[CONF_LOW_FLOW_COOLDOWN_S]))] = s_int(
        min_=0, step=1
    )

    existing_clear = ex.get(CONF_LOW_FLOW_CLEAR_ON_HIGH_S, None)
    if existing_clear in (None, ""):
        key = vol.Optional(CONF_LOW_FLOW_CLEAR_ON_HIGH_S)
    else:
        key = vol.Optional(CONF_LOW_FLOW_CLEAR_ON_HIGH_S, default=str(existing_clear))
    fields[key] = s_text()

    return vol.Schema(fields)


def _tank_leak_schema(existing: Optional[Dict[str, Any]] = None) -> vol.Schema:
    ex = existing or {}
    fields: Dict[Any, Any] = {}

    fields[vol.Required(
        CONF_TANK_LEAK_MIN_REFILL_VOLUME,
        default=ex.get(CONF_TANK_LEAK_MIN_REFILL_VOLUME, DEFAULTS[CONF_TANK_LEAK_MIN_REFILL_VOLUME])
    )] = s_number(min_=0.01, step=0.01)

    fields[vol.Required(
        CONF_TANK_LEAK_MAX_REFILL_VOLUME,
        default=ex.get(CONF_TANK_LEAK_MAX_REFILL_VOLUME, DEFAULTS[CONF_TANK_LEAK_MAX_REFILL_VOLUME])
    )] = s_number(min_=0, step=0.01)

    fields[vol.Required(
        CONF_TANK_LEAK_TOLERANCE_PCT,
        default=ex.get(CONF_TANK_LEAK_TOLERANCE_PCT, DEFAULTS[CONF_TANK_LEAK_TOLERANCE_PCT])
    )] = s_number(min_=1, step=1)

    fields[vol.Required(
        CONF_TANK_LEAK_REPEAT_COUNT,
        default=ex.get(CONF_TANK_LEAK_REPEAT_COUNT, DEFAULTS[CONF_TANK_LEAK_REPEAT_COUNT])
    )] = s_int(min_=2, step=1)

    fields[vol.Required(
        CONF_TANK_LEAK_WINDOW_S,
        default=ex.get(CONF_TANK_LEAK_WINDOW_S, DEFAULTS[CONF_TANK_LEAK_WINDOW_S])
    )] = s_int(min_=60, step=60)

    fields[vol.Required(
        CONF_TANK_LEAK_CLEAR_IDLE_S,
        default=ex.get(CONF_TANK_LEAK_CLEAR_IDLE_S, DEFAULTS[CONF_TANK_LEAK_CLEAR_IDLE_S])
    )] = s_int(min_=60, step=60)

    fields[vol.Required(
        CONF_TANK_LEAK_COOLDOWN_S,
        default=ex.get(CONF_TANK_LEAK_COOLDOWN_S, DEFAULTS[CONF_TANK_LEAK_COOLDOWN_S])
    )] = s_int(min_=0, step=60)

    fields[vol.Required(
        CONF_TANK_LEAK_MIN_REFILL_DURATION_S,
        default=ex.get(CONF_TANK_LEAK_MIN_REFILL_DURATION_S, DEFAULTS[CONF_TANK_LEAK_MIN_REFILL_DURATION_S])
    )] = s_int(min_=0, step=1)
    fields[vol.Required(
        CONF_TANK_LEAK_MAX_REFILL_DURATION_S,
        default=ex.get(CONF_TANK_LEAK_MAX_REFILL_DURATION_S, DEFAULTS[CONF_TANK_LEAK_MAX_REFILL_DURATION_S])
    )] = s_int(min_=0, step=1)

    return vol.Schema(fields)


def _intelligent_schema(existing: Optional[Dict[str, Any]] = None) -> vol.Schema:
    ex = existing or {}
    fields: Dict[Any, Any] = {}

    # Optional occupancy mode entity (input_select preferred)
    existing_occ = ex.get(CONF_OCC_MODE_ENTITY, None)
    if existing_occ in (None, ""):
        fields[vol.Optional(CONF_OCC_MODE_ENTITY)] = s_entity("input_select")
    else:
        fields[vol.Optional(CONF_OCC_MODE_ENTITY, default=existing_occ)] = s_entity("input_select")

    # Optional CSV state lists
    away_default = ex.get(CONF_OCC_STATE_AWAY, DEFAULTS.get(CONF_OCC_STATE_AWAY, ""))
    vac_default = ex.get(CONF_OCC_STATE_VACATION, DEFAULTS.get(CONF_OCC_STATE_VACATION, ""))
    if away_default:
        fields[vol.Optional(CONF_OCC_STATE_AWAY, default=away_default)] = s_text()
    else:
        fields[vol.Optional(CONF_OCC_STATE_AWAY)] = s_text()
    if vac_default:
        fields[vol.Optional(CONF_OCC_STATE_VACATION, default=vac_default)] = s_text()
    else:
        fields[vol.Optional(CONF_OCC_STATE_VACATION)] = s_text()

    fields[vol.Required(
        CONF_INTEL_LEARNING_ENABLE,
        default=ex.get(CONF_INTEL_LEARNING_ENABLE, DEFAULTS[CONF_INTEL_LEARNING_ENABLE])
    )] = s_bool()

    return vol.Schema(fields)


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._data: Dict[str, Any] = {}
        self._low_flow_enabled = False
        self._tank_leak_enabled = False
        self._intel_enabled = False

    async def async_step_user(self, user_input: Optional[Dict[str, Any]] = None):
        errors: Dict[str, str] = {}
        if user_input is not None:
            # Sanitize optional hot water: treat empty strings as absent
            if CONF_HOT_WATER_SENSOR in user_input and not user_input[CONF_HOT_WATER_SENSOR]:
                user_input.pop(CONF_HOT_WATER_SENSOR, None)
            # Sanitize optional occupancy mode entity
            if CONF_OCC_MODE_ENTITY in user_input and not user_input[CONF_OCC_MODE_ENTITY]:
                user_input.pop(CONF_OCC_MODE_ENTITY, None)

            self._data.update(user_input)
            self._low_flow_enabled = bool(user_input.get(CONF_LOW_FLOW_ENABLE, False))
            self._tank_leak_enabled = bool(user_input.get(CONF_TANK_LEAK_ENABLE, False))
            self._intel_enabled = bool(user_input.get(CONF_INTEL_DETECT_ENABLE, False))
            if self._low_flow_enabled:
                return await self.async_step_low_flow()
            if self._tank_leak_enabled:
                return await self.async_step_tank_leak()
            if self._intel_enabled:
                return await self.async_step_intelligent()
            return self.async_create_entry(title=self._data.get(CONF_SENSOR_PREFIX) or "Water Monitor", data=self._data)

        return self.async_show_form(step_id="user", data_schema=_main_schema(), errors=errors)

    async def async_step_low_flow(self, user_input: Optional[Dict[str, Any]] = None):
        """Step to configure low-flow leak sensor settings."""
        if user_input is not None:
            # Clean optional field to int or None
            user_input[CONF_LOW_FLOW_CLEAR_ON_HIGH_S] = _clean_optional_seconds(
                user_input.get(CONF_LOW_FLOW_CLEAR_ON_HIGH_S)
            )
            self._data.update(user_input)
            if self._tank_leak_enabled:
                return await self.async_step_tank_leak()
            if self._intel_enabled:
                return await self.async_step_intelligent()
            return self.async_create_entry(
                title=self._data.get(CONF_SENSOR_PREFIX) or "Water Monitor",
                data=self._data,
            )

        return self.async_show_form(step_id="low_flow", data_schema=_low_flow_schema(self._data))

    async def async_step_tank_leak(self, user_input: Optional[Dict[str, Any]] = None):
        if user_input is not None:
            self._data.update(user_input)
            if self._intel_enabled:
                return await self.async_step_intelligent()
            return self.async_create_entry(title=self._data.get(CONF_SENSOR_PREFIX) or "Water Monitor", data=self._data)

        return self.async_show_form(step_id="tank_leak", data_schema=_tank_leak_schema(self._data))

    async def async_step_intelligent(self, user_input: Optional[Dict[str, Any]] = None):
        if user_input is not None:
            # optional occupancy entity and CSV text fields
            if CONF_OCC_MODE_ENTITY in user_input and not user_input[CONF_OCC_MODE_ENTITY]:
                user_input.pop(CONF_OCC_MODE_ENTITY, None)
            for k in (CONF_OCC_STATE_AWAY, CONF_OCC_STATE_VACATION):
                if k in user_input and isinstance(user_input[k], str):
                    norm = ", ".join([p.strip() for p in user_input[k].split(",") if p.strip()])
                    if norm:
                        user_input[k] = norm
                    else:
                        user_input.pop(k, None)
            self._data.update(user_input)
            return self.async_create_entry(title=self._data.get(CONF_SENSOR_PREFIX) or "Water Monitor", data=self._data)

        return self.async_show_form(step_id="intelligent", data_schema=_intelligent_schema(self._data))

    @staticmethod
    def async_get_options_flow(entry: config_entries.ConfigEntry):
        return WaterMonitorOptionsFlow(entry)


class WaterMonitorOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._entry = config_entry
        self._existing = {**config_entry.data, **config_entry.options}
        self._opts: Dict[str, Any] = {}
        self._low_flow_enabled = bool(self._existing.get(CONF_LOW_FLOW_ENABLE, DEFAULTS[CONF_LOW_FLOW_ENABLE]))
        self._tank_leak_enabled = bool(self._existing.get(CONF_TANK_LEAK_ENABLE, DEFAULTS[CONF_TANK_LEAK_ENABLE]))
        self._intel_enabled = bool(self._existing.get(CONF_INTEL_DETECT_ENABLE, DEFAULTS[CONF_INTEL_DETECT_ENABLE]))

    @callback
    def _store(self):
        return self.async_create_entry(title="", data=self._opts)

    async def async_step_init(self, user_input: Optional[Dict[str, Any]] = None):
        if user_input is not None:
            # Sanitize optional hot water: treat empty strings as absent
            if CONF_HOT_WATER_SENSOR in user_input and not user_input[CONF_HOT_WATER_SENSOR]:
                user_input.pop(CONF_HOT_WATER_SENSOR, None)
            # Sanitize optional occupancy mode entity
            if CONF_OCC_MODE_ENTITY in user_input and not user_input[CONF_OCC_MODE_ENTITY]:
                user_input.pop(CONF_OCC_MODE_ENTITY, None)

            self._opts.update(user_input)
            self._low_flow_enabled = bool(user_input.get(CONF_LOW_FLOW_ENABLE, DEFAULTS[CONF_LOW_FLOW_ENABLE]))
            self._tank_leak_enabled = bool(user_input.get(CONF_TANK_LEAK_ENABLE, DEFAULTS[CONF_TANK_LEAK_ENABLE]))
            self._intel_enabled = bool(user_input.get(CONF_INTEL_DETECT_ENABLE, DEFAULTS[CONF_INTEL_DETECT_ENABLE]))
            if self._low_flow_enabled:
                return await self.async_step_low_flow()
            if self._tank_leak_enabled:
                return await self.async_step_tank_leak()
            if self._intel_enabled:
                return await self.async_step_intelligent()
            return self._store()

        defaults = {
            k: self._existing.get(k, DEFAULTS.get(k))
            for k in [
                CONF_SENSOR_PREFIX,
                CONF_FLOW_SENSOR,
                CONF_VOLUME_SENSOR,
                CONF_HOT_WATER_SENSOR,
                CONF_MIN_SESSION_VOLUME,
                CONF_MIN_SESSION_DURATION,
                CONF_SESSION_GAP_TOLERANCE,
                CONF_SESSION_CONTINUITY_WINDOW,
                CONF_SESSIONS_USE_BASELINE_AS_ZERO,
                CONF_SESSIONS_IDLE_TO_CLOSE_S,
                CONF_LOW_FLOW_ENABLE,
                CONF_TANK_LEAK_ENABLE,
                CONF_INTEL_DETECT_ENABLE,
            ]
        }
        return self.async_show_form(step_id="init", data_schema=_main_schema(defaults))

    async def async_step_low_flow(self, user_input: Optional[Dict[str, Any]] = None):
        if user_input is not None:
            user_input[CONF_LOW_FLOW_CLEAR_ON_HIGH_S] = _clean_optional_seconds(
                user_input.get(CONF_LOW_FLOW_CLEAR_ON_HIGH_S)
            )
            self._opts.update(user_input)
            if self._tank_leak_enabled:
                return await self.async_step_tank_leak()
            return self._store()

        defaults = {
            k: self._existing.get(k, DEFAULTS.get(k))
            for k in [
                CONF_LOW_FLOW_MAX_FLOW,
                CONF_LOW_FLOW_SEED_S,
                CONF_LOW_FLOW_MIN_S,
                CONF_LOW_FLOW_CLEAR_IDLE_S,
                CONF_LOW_FLOW_COUNTING_MODE,
                CONF_LOW_FLOW_SMOOTHING_S,
                CONF_LOW_FLOW_BASELINE_MARGIN_PCT,
                CONF_LOW_FLOW_COOLDOWN_S,
                CONF_LOW_FLOW_CLEAR_ON_HIGH_S,
            ]
        }
        return self.async_show_form(step_id="low_flow", data_schema=_low_flow_schema(defaults))

    async def async_step_tank_leak(self, user_input: Optional[Dict[str, Any]] = None):
        if user_input is not None:
            self._opts.update(user_input)
            if self._intel_enabled:
                return await self.async_step_intelligent()
            return self._store()

        defaults = {
            k: self._existing.get(k, DEFAULTS.get(k))
            for k in [
                CONF_TANK_LEAK_MIN_REFILL_VOLUME,
                CONF_TANK_LEAK_MAX_REFILL_VOLUME,
                CONF_TANK_LEAK_TOLERANCE_PCT,
                CONF_TANK_LEAK_REPEAT_COUNT,
                CONF_TANK_LEAK_WINDOW_S,
                CONF_TANK_LEAK_CLEAR_IDLE_S,
                CONF_TANK_LEAK_COOLDOWN_S,
                CONF_TANK_LEAK_MIN_REFILL_DURATION_S,
                CONF_TANK_LEAK_MAX_REFILL_DURATION_S,
            ]
        }
        return self.async_show_form(step_id="tank_leak", data_schema=_tank_leak_schema(defaults))

    async def async_step_intelligent(self, user_input: Optional[Dict[str, Any]] = None):
        if user_input is not None:
            if CONF_OCC_MODE_ENTITY in user_input and not user_input[CONF_OCC_MODE_ENTITY]:
                user_input.pop(CONF_OCC_MODE_ENTITY, None)
            for k in (CONF_OCC_STATE_AWAY, CONF_OCC_STATE_VACATION):
                if k in user_input and isinstance(user_input[k], str):
                    norm = ", ".join([p.strip() for p in user_input[k].split(",") if p.strip()])
                    if norm:
                        user_input[k] = norm
                    else:
                        user_input.pop(k, None)
            self._opts.update(user_input)
            return self._store()

        defaults = {
            k: self._existing.get(k, DEFAULTS.get(k))
            for k in [
                CONF_OCC_MODE_ENTITY,
                CONF_OCC_STATE_AWAY,
                CONF_OCC_STATE_VACATION,
                CONF_INTEL_LEARNING_ENABLE,
            ]
        }
        return self.async_show_form(step_id="intelligent", data_schema=_intelligent_schema(defaults))