from __future__ import annotations

from typing import Optional

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, CONF_SENSOR_PREFIX, CONF_SYNTHETIC_ENABLE


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    # Create expected baseline and leak sensitivity controls per entry
    entities = [ExpectedBaselineNumber(entry), LeakSensitivityNumber(entry)]
    ex = {**entry.data, **entry.options}
    if bool(ex.get(CONF_SYNTHETIC_ENABLE, False)):
        entities.append(SyntheticFlowNumber(entry))
    async_add_entities(entities)


class ExpectedBaselineNumber(NumberEntity):
    """User-settable expected low-flow baseline (0 disables)."""

    _attr_native_step = 0.01
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0.0
    _attr_native_max_value = 1000.0  # generous upper bound
    _attr_unit_of_measurement = None  # unit depends on upstream flow sensor; kept out to avoid mismatch

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        ex = {**entry.data, **entry.options}
        prefix = ex.get(CONF_SENSOR_PREFIX) or entry.title or "Water Monitor"
        self._attr_name = f"{prefix} Expected low-flow baseline"
        self._attr_unique_id = f"{entry.entry_id}_expected_baseline"
        self._value: float = 0.0

    @property
    def device_info(self) -> DeviceInfo:
        ex = {**self._entry.data, **self._entry.options}
        prefix = ex.get(CONF_SENSOR_PREFIX) or self._entry.title or "Water Monitor"
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name=prefix,
            manufacturer="markaggar",
            model="Water Session Tracking and Leak Detection",
        )

    @property
    def native_value(self) -> float | None:
        return float(self._value)

    async def async_set_native_value(self, value: float) -> None:
        self._value = max(0.0, float(value))
        self.async_write_ha_state()


class LeakSensitivityNumber(NumberEntity):
    """Controls how early the intelligent leak detector alerts (higher = earlier)."""

    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = 0.0
    _attr_native_max_value = 100.0
    _attr_native_step = 5.0

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        ex = {**entry.data, **entry.options}
        prefix = ex.get(CONF_SENSOR_PREFIX) or entry.title or "Water Monitor"
        self._attr_name = f"{prefix} Leak alert sensitivity"
        self._attr_unique_id = f"{entry.entry_id}_leak_sensitivity"
        self._value: float = 50.0  # default midpoint

    @property
    def device_info(self) -> DeviceInfo:
        ex = {**self._entry.data, **self._entry.options}
        prefix = ex.get(CONF_SENSOR_PREFIX) or self._entry.title or "Water Monitor"
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name=prefix,
            manufacturer="markaggar",
            model="Water Session Tracking and Leak Detection",
        )

    @property
    def native_value(self) -> float | None:
        return float(self._value)

    async def async_set_native_value(self, value: float) -> None:
        try:
            v = float(value)
        except Exception:
            v = 50.0
        # Clamp and round to step to keep expectations stable
        v = max(0.0, min(100.0, v))
        # snap to nearest step (5.0)
        v = round(v / 5.0) * 5.0
        self._value = v
        self.async_write_ha_state()


class SyntheticFlowNumber(NumberEntity):
    """Integration-owned synthetic flow control (gpm)."""

    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0.0
    _attr_native_max_value = 50.0
    _attr_native_step = 0.01

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        ex = {**entry.data, **entry.options}
        prefix = ex.get(CONF_SENSOR_PREFIX) or entry.title or "Water Monitor"
        self._attr_name = f"{prefix} Synthetic flow (gpm)"
        self._attr_unique_id = f"{entry.entry_id}_synthetic_flow_gpm"
        self._value: float = 0.0

    @property
    def device_info(self) -> DeviceInfo:
        ex = {**self._entry.data, **self._entry.options}
        prefix = ex.get(CONF_SENSOR_PREFIX) or self._entry.title or "Water Monitor"
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name=prefix,
            manufacturer="markaggar",
            model="Water Session Tracking and Leak Detection",
        )

    async def async_added_to_hass(self) -> None:
        # Initialize from stored value if present
        try:
            domain_data = self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id)
            if isinstance(domain_data, dict):
                v = domain_data.get("synthetic_flow_gpm")
                if isinstance(v, (int, float)):
                    self._value = max(0.0, float(v))
        except Exception:
            pass
        self.async_write_ha_state()

    @property
    def native_value(self) -> float | None:
        return float(self._value)

    async def async_set_native_value(self, value: float) -> None:
        try:
            v = max(0.0, float(value))
        except Exception:
            v = 0.0
        self._value = v
        # Persist in domain data for quick access by sensors/engine
        try:
            ddomain = self.hass.data.setdefault(DOMAIN, {})
            edata = ddomain.setdefault(self._entry.entry_id, {})
            edata["synthetic_flow_gpm"] = v
        except Exception:
            pass
        self.async_write_ha_state()
