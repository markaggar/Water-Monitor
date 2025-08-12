"""Water Monitor binary sensors (phase 1: upstream health only)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event

from .const import (
    DOMAIN,
    CONF_SENSOR_PREFIX,
    CONF_FLOW_SENSOR,
    CONF_VOLUME_SENSOR,
    CONF_HOT_WATER_SENSOR,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up upstream health binary sensor from a config entry."""
    opts = {**entry.data, **entry.options}
    prefix = opts.get(CONF_SENSOR_PREFIX) or "Water Monitor"
    flow_sensor = opts.get(CONF_FLOW_SENSOR)
    volume_sensor = opts.get(CONF_VOLUME_SENSOR)
    hot_water_sensor = opts.get(CONF_HOT_WATER_SENSOR)

    async_add_entities(
        [
            UpstreamHealthBinarySensor(
                entry=entry,
                name=f"{prefix} Upstream sensors health",
                flow_entity_id=flow_sensor,
                volume_entity_id=volume_sensor,
                hot_water_entity_id=hot_water_sensor,
            )
        ]
    )


class UpstreamHealthBinarySensor(BinarySensorEntity):
    """Reports health of upstream sensors with per-name last OK timestamps."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(
        self,
        entry: ConfigEntry,
        name: str,
        flow_entity_id: Optional[str],
        volume_entity_id: Optional[str],
        hot_water_entity_id: Optional[str],
    ) -> None:
        self._entry = entry
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_upstream_health"
        self._attr_is_on = True
        self._attr_available = True
        self._attr_extra_state_attributes = {}

        self._flow_entity_id = flow_entity_id
        self._volume_entity_id = volume_entity_id
        self._hot_water_entity_id = hot_water_entity_id or None

        self._unsub_state = None
        self._last_ok: dict[str, Optional[datetime]] = {}

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
    def is_on(self) -> bool:
        return self._attr_is_on

    async def async_added_to_hass(self) -> None:
        self._attr_is_on = True
        self._attr_available = True
        self.async_write_ha_state()
        tracked = [
            e
            for e in [self._flow_entity_id, self._volume_entity_id, self._hot_water_entity_id]
            if e
        ]
        if tracked:
            self._unsub_state = async_track_state_change_event(
                self.hass, tracked, self._async_source_changed
            )
        # Evaluate once at start
        await self._evaluate(datetime.now(timezone.utc))

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_state:
            self._unsub_state()
            self._unsub_state = None
        await super().async_will_remove_from_hass()

    @callback
    async def _async_source_changed(self, event) -> None:
        await self._evaluate(datetime.now(timezone.utc))

    def _is_flow_ok(self) -> bool:
        st = self.hass.states.get(self._flow_entity_id) if self._flow_entity_id else None
        if not st or st.state in (None, "unknown", "unavailable"):
            return False
        try:
            float(st.state)
            return True
        except (ValueError, TypeError):
            return False

    def _is_volume_ok(self) -> bool:
        st = self.hass.states.get(self._volume_entity_id) if self._volume_entity_id else None
        if not st or st.state in (None, "unknown", "unavailable"):
            return False
        try:
            float(st.state)
            return True
        except (ValueError, TypeError):
            return False

    def _is_hot_ok(self) -> Optional[bool]:
        if not self._hot_water_entity_id:
            return None
        st = self.hass.states.get(self._hot_water_entity_id)
        if not st or st.state in (None, "unknown", "unavailable"):
            return False
        return str(st.state).lower() in ("on", "off", "true", "false", "0", "1")

    async def _evaluate(self, now: datetime) -> None:
        unavailable: list[str] = []
        unknown: list[str] = []
        per_name_last_ok: dict[str, Optional[str]] = {}
        name_to_entity: dict[str, str] = {}

        # Helper to record
        def upd(ent_id: Optional[str], ok: Optional[bool]):
            if not ent_id:
                return
            st = self.hass.states.get(ent_id)
            friendly = (st.attributes.get("friendly_name") if st else None) or ent_id
            name_to_entity[friendly] = ent_id
            if ok is True:
                self._last_ok[ent_id] = now
            if ok is False:
                if not st or st.state == "unavailable":
                    unavailable.append(ent_id)
                elif not st or st.state == "unknown":
                    unknown.append(ent_id)
            last = self._last_ok.get(ent_id)
            per_name_last_ok[friendly] = last.isoformat() if last else None

        upd(self._flow_entity_id, self._is_flow_ok())
        upd(self._volume_entity_id, self._is_volume_ok())
        hot_ok = self._is_hot_ok()
        if self._hot_water_entity_id is not None:
            upd(self._hot_water_entity_id, hot_ok)

        status = (
            (self._flow_entity_id is None or self._is_flow_ok())
            and (self._volume_entity_id is None or self._is_volume_ok())
            and (self._hot_water_entity_id is None or hot_ok is True)
        )
        self._attr_is_on = bool(status)
        self._attr_extra_state_attributes = {
            "unavailable_entities": unavailable,
            "unknown_entities": unknown,
            "name_to_entity": name_to_entity,
            **per_name_last_ok,
        }
        self.async_write_ha_state()
