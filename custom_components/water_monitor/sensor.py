"""Water Monitor sensors."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Callable

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)

from .const import (
    DOMAIN,
    CONF_FLOW_SENSOR,
    CONF_VOLUME_SENSOR,
    CONF_HOT_WATER_SENSOR,
    CONF_MIN_SESSION_VOLUME,
    CONF_MIN_SESSION_DURATION,
    CONF_SESSION_GAP_TOLERANCE,
    CONF_SESSION_CONTINUITY_WINDOW,
    CONF_SENSOR_PREFIX,
    UPDATE_INTERVAL,
)
from .water_session_tracker import WaterSessionTracker

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Water Monitor sensors."""
    def _get(key: str, default: Any | None = None) -> Any:
        return config_entry.options.get(key, config_entry.data.get(key, default))

    flow_sensor = _get(CONF_FLOW_SENSOR, "")
    volume_sensor = _get(CONF_VOLUME_SENSOR, "")
    hot_water_sensor = _get(CONF_HOT_WATER_SENSOR, "")
    min_session_volume = float(_get(CONF_MIN_SESSION_VOLUME, 0.0))
    min_session_duration = int(_get(CONF_MIN_SESSION_DURATION, 0))
    session_gap_tolerance = int(_get(CONF_SESSION_GAP_TOLERANCE, 5))
    session_continuity_window = int(_get(CONF_SESSION_CONTINUITY_WINDOW, 3))
    sensor_prefix = _get(CONF_SENSOR_PREFIX, config_entry.title or "Water Monitor")

    main_sensor = WaterSessionSensor(
        entry=config_entry,
        flow_sensor=flow_sensor,
        volume_sensor=volume_sensor,
        hot_water_sensor=hot_water_sensor,
        min_session_volume=min_session_volume,
        min_session_duration=min_session_duration,
        session_gap_tolerance=session_gap_tolerance,
        session_continuity_window=session_continuity_window,
        name=f"{sensor_prefix} Last session volume",
        unique_suffix="last_session",
    )

    current_sensor = CurrentSessionVolumeSensor(
        entry=config_entry,
        name=f"{sensor_prefix} Current session volume",
        unique_suffix="current_session",
    )

    # Link them together
    main_sensor.set_current_session_callback(current_sensor.update_from_tracker)

    # New last-session metrics sensors (always created; no user options)
    tracked = [e for e in [flow_sensor, volume_sensor, hot_water_sensor] if e]
    duration_sensor = LastSessionDurationSensor(
        entry=config_entry,
        name=f"{sensor_prefix} Last session duration",
        unique_suffix="last_session_duration",
        tracked_entities=tracked,
    )
    avg_flow_sensor = LastSessionAverageFlowSensor(
        entry=config_entry,
        name=f"{sensor_prefix} Last session average flow",
        unique_suffix="last_session_avg_flow",
        tracked_entities=tracked,
    )
    hot_pct_sensor = LastSessionHotWaterPctSensor(
        entry=config_entry,
        name=f"{sensor_prefix} Last session hot water percentage",
        unique_suffix="last_session_hot_pct",
        tracked_entities=tracked,
    )

    # Register listeners so they stay in sync with the tracker
    main_sensor.add_state_listener(duration_sensor.update_from_tracker)
    main_sensor.add_state_listener(avg_flow_sensor.update_from_tracker)
    main_sensor.add_state_listener(hot_pct_sensor.update_from_tracker)

    async_add_entities([main_sensor, current_sensor, duration_sensor, avg_flow_sensor, hot_pct_sensor])


class WaterSessionSensor(SensorEntity):
    """Water session tracking sensor - shows last completed session volume."""

    _attr_icon = "mdi:water"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 2  # show 2 decimals in UI

    def __init__(
        self,
        entry: ConfigEntry,
        flow_sensor: str,
        volume_sensor: str,
        hot_water_sensor: str,
        min_session_volume: float,
        min_session_duration: int,
        session_gap_tolerance: int,
        session_continuity_window: int,
        name: str,
        unique_suffix: str,
    ):
        """Initialize the sensor."""
        self._entry = entry
        self._flow_sensor = flow_sensor
        self._volume_sensor = volume_sensor
        self._hot_water_sensor = hot_water_sensor

        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{unique_suffix}"
        self._attr_native_value = 0.0
        self._attr_extra_state_attributes = {}
        self._attr_available = True
        self._attr_native_unit_of_measurement = None  # Set dynamically from volume sensor

        # Initialize the water session tracker
        self._tracker = WaterSessionTracker(
            min_session_volume=min_session_volume,
            min_session_duration=min_session_duration,
            session_gap_tolerance=session_gap_tolerance,
            session_continuity_window=session_continuity_window,
        )

        # Track entities we're listening to
        self._tracked_entities = (
            [flow_sensor, volume_sensor, hot_water_sensor]
            if hot_water_sensor
            else [flow_sensor, volume_sensor]
        )

        # Periodic update tracking
        self._periodic_update_unsub = None
        self._needs_periodic_updates = False

        # Callbacks for dependent sensors (current session + metrics sensors)
        self._listeners = []

    # Note: listeners are initialized per-instance in __init__

    @property
    def device_info(self) -> DeviceInfo:
        """Group both sensors under the integration device."""
        ex = {**self._entry.data, **self._entry.options}
        prefix = ex.get(CONF_SENSOR_PREFIX) or self._entry.title or "Water Monitor"
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name=prefix,
            manufacturer="markaggar",
            model="Water Session Tracking and Leak Detection",
        )

    @property
    def native_value(self):
        """Return the native value of the sensor."""
        return self._attr_native_value

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self._attr_available

    def set_current_session_callback(self, callback: Callable):
        """Back-compat: add a single listener (current session sensor)."""
        # Keep the method name for backward compatibility, but support multiple listeners internally.
        self.add_state_listener(callback)

    def add_state_listener(self, callback: Callable[[dict], Any]):
        """Register a listener to receive tracker state_data updates."""
        if callback not in self._listeners:
            self._listeners.append(callback)

    async def async_added_to_hass(self) -> None:
        """Register callbacks when entity is added to hass."""
        self._attr_native_value = 0.0
        self._attr_available = True
        self.async_write_ha_state()

        if self._tracked_entities:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass,
                    self._tracked_entities,
                    self._async_sensor_changed,
                )
            )

        # Defer the initial update so other entities finish being added
        self.hass.async_create_task(self._async_update_from_sensors())

    async def _async_sensor_changed(self, event) -> None:
        """Handle sensor state changes."""
        await self._async_update_from_sensors()

    def _schedule_periodic_updates(self, reason: str):
        """Schedule periodic updates when timing is critical."""
        if self._periodic_update_unsub is None:
            _LOGGER.debug("Starting periodic updates: %s", reason)
            self._periodic_update_unsub = async_track_time_interval(
                self.hass, self._async_periodic_update, timedelta(seconds=UPDATE_INTERVAL)
            )

    def _cancel_periodic_updates(self, reason: str):
        """Cancel periodic updates."""
        if self._periodic_update_unsub is not None:
            _LOGGER.debug("Stopping periodic updates: %s", reason)
            self._periodic_update_unsub()
            self._periodic_update_unsub = None

    @callback
    async def _async_periodic_update(self, now: datetime) -> None:
        """Handle periodic updates for gap monitoring and session continuation."""
        await self._async_update_from_sensors()

    def _should_use_periodic_updates(self, state_data: dict) -> tuple[bool, str]:
        """Determine if periodic updates are needed based on current state."""
        gap_active = state_data.get("gap_active", False)
        session_active = state_data.get("current_session_active", False)
        flow_rate = state_data.get("flow_sensor_value", 0.0)

        if gap_active:
            return True, "gap monitoring"
        if session_active and flow_rate == 0 and not gap_active:
            return True, "session continuation monitoring"
        return False, "no timing operations needed"

    async def _async_update_from_sensors(self) -> None:
        """Update the sensor from tracked entities."""
        # Get current sensor states
        flow_state = self.hass.states.get(self._flow_sensor)
        volume_state = self.hass.states.get(self._volume_sensor)
        hot_water_state = self.hass.states.get(self._hot_water_sensor) if self._hot_water_sensor else None

        # Check if required sensors are available
        if not all([flow_state, volume_state]):
            return

        # Check if states are ready
        if (
            flow_state.state in [None, "unavailable", "unknown"]
            or volume_state.state in [None, "unavailable", "unknown"]
            or (hot_water_state and hot_water_state.state in [None, "unavailable", "unknown"])
        ):
            return

        try:
            # Parse sensor values
            flow_rate = float(flow_state.state)
            volume_total = float(volume_state.state)
            hot_water_active = False
            if hot_water_state:
                s = str(hot_water_state.state).lower()
                hot_water_active = s in ("on", "true", "1")

            current_time = datetime.now(timezone.utc)

            # Determine unit from the volume sensor
            volume_unit = volume_state.attributes.get("unit_of_measurement")
            if volume_unit:
                self._attr_native_unit_of_measurement = volume_unit

            # Update the tracker
            state_data = self._tracker.update(
                flow_rate=flow_rate,
                volume_total=volume_total,
                hot_water_active=hot_water_active,
                timestamp=current_time,
            )

            # Add unit info so the current session sensor can sync units
            state_data["volume_unit"] = volume_unit

            # Update this entity's state and attributes (last completed session volume)
            last_session_volume = float(state_data.get("last_session_volume", 0.0))
            self._attr_native_value = round(last_session_volume, 2)
            self._attr_extra_state_attributes = state_data
            self._attr_available = True

            # Notify all listeners (dependent sensors)
            for cb in list(self._listeners):
                try:
                    await cb(state_data)
                except Exception as err:
                    _LOGGER.exception("Listener update failed: %s", err)

            # Decide periodic update scheduling
            should_update, reason = self._should_use_periodic_updates(state_data)
            if should_update and not self._needs_periodic_updates:
                self._needs_periodic_updates = True
                self._schedule_periodic_updates(reason)
            elif not should_update and self._needs_periodic_updates:
                self._needs_periodic_updates = False
                self._cancel_periodic_updates(reason)

            # Write state
            self.async_write_ha_state()

        except (ValueError, TypeError) as e:
            _LOGGER.error("Error parsing sensor values: %s", e)

    async def async_will_remove_from_hass(self) -> None:
        """Clean up when entity is removed."""
        if self._periodic_update_unsub is not None:
            self._cancel_periodic_updates("entity removal")
        await super().async_will_remove_from_hass()


class CurrentSessionVolumeSensor(SensorEntity):
    """Current session volume sensor - shows real-time session volume."""

    _attr_icon = "mdi:water-pump"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 2  # show 2 decimals in UI

    def __init__(self, entry: ConfigEntry, name: str, unique_suffix: str) -> None:
        self._entry = entry
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{unique_suffix}"
        self._attr_native_value = 0.0
        self._attr_extra_state_attributes = {}
        self._attr_available = True
        self._attr_native_unit_of_measurement = None  # Set dynamically from volume sensor via main sensor

    @property
    def device_info(self) -> DeviceInfo:
        """Group both sensors under the integration device."""
        ex = {**self._entry.data, **self._entry.options}
        prefix = ex.get(CONF_SENSOR_PREFIX) or self._entry.title or "Water Monitor"
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name=prefix,
            manufacturer="markaggar",
            model="Water Session Tracking and Leak Detection",
        )

    @property
    def native_value(self):
        """Return the native value of the sensor."""
        return self._attr_native_value

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self._attr_available

    async def async_added_to_hass(self) -> None:
        """Set initial state when added to hass."""
        self._attr_native_value = 0.0
        self._attr_available = True
        self.async_write_ha_state()

    def _calculate_current_volume(self, state_data: dict) -> float:
        """Calculate the most recent/relevant session volume for the state."""
        current_volume = state_data.get("current_session_volume", 0.0)
        intermediate_volume = state_data.get("intermediate_session_volume", 0.0)
        session_active = state_data.get("current_session_active", False)
        intermediate_exists = state_data.get("intermediate_session_exists", False)

        if session_active and current_volume > 0:
            return float(current_volume)
        elif intermediate_exists and intermediate_volume > 0:
            return float(intermediate_volume)
        else:
            # Session ended (or not started) -> zero
            return 0.0

    def _triage_stage(self, state_data: dict) -> str:
        """Which stage should drive attributes: current, intermediate, or final."""
        if state_data.get("current_session_active", False) and state_data.get("current_session_volume", 0.0) > 0:
            return "current"
        if state_data.get("intermediate_session_exists", False) and state_data.get("intermediate_session_volume", 0.0) > 0:
            return "intermediate"
        return "final"

    async def update_from_tracker(self, state_data: dict):
        """Update this sensor when main sensor updates."""
        # Sync the unit from the main sensor (derived from source volume sensor)
        unit = state_data.get("volume_unit")
        if unit:
            self._attr_native_unit_of_measurement = unit

        current_volume = self._calculate_current_volume(state_data)
        stage = self._triage_stage(state_data)

        # Choose attributes based on stage
        if stage == "current":
            session_duration = int(state_data.get("current_session_duration", 0))
            session_avg_flow = float(state_data.get("current_session_average_flow", 0.0))
            session_hot_pct = float(state_data.get("current_session_hot_water_pct", 0.0))
        elif stage == "intermediate":
            session_duration = int(state_data.get("intermediate_session_duration", 0))
            session_avg_flow = float(state_data.get("intermediate_session_average_flow", 0.0))
            session_hot_pct = float(state_data.get("intermediate_session_hot_water_pct", 0.0))
        else:
            session_duration = int(state_data.get("last_session_duration", 0))
            session_avg_flow = float(state_data.get("last_session_average_flow", 0.0))
            session_hot_pct = float(state_data.get("last_session_hot_water_pct", 0.0))

        # Attributes summary (keep debug fields for troubleshooting)
        self._attr_extra_state_attributes = {
            # Core, triaged session metrics
            "session_stage": stage,
            "session_duration": session_duration,
            "session_average_flow": session_avg_flow,
            "session_hot_water_pct": session_hot_pct,
            # Instantaneous and debug
            "flow_sensor_value": state_data.get("flow_sensor_value", 0.0),
            "current_session_active": state_data.get("current_session_active", False),
            "intermediate_session_exists": state_data.get("intermediate_session_exists", False),
            "debug_state": state_data.get("debug_state", "UNKNOWN"),
            # Raw values (for transparency and UI experimentation)
            "current_session_volume": state_data.get("current_session_volume", 0.0),
            "current_session_duration": state_data.get("current_session_duration", 0),
            "current_session_average_flow": state_data.get("current_session_average_flow", 0.0),
            "current_session_hot_water_pct": state_data.get("current_session_hot_water_pct", 0.0),
            "intermediate_session_volume": state_data.get("intermediate_session_volume", 0.0),
            "intermediate_session_duration": state_data.get("intermediate_session_duration", 0),
            "intermediate_session_average_flow": state_data.get("intermediate_session_average_flow", 0.0),
            "intermediate_session_hot_water_pct": state_data.get("intermediate_session_hot_water_pct", 0.0),
            "last_session_volume": state_data.get("last_session_volume", 0.0),
            "last_session_duration": state_data.get("last_session_duration", 0),
            "last_session_average_flow": state_data.get("last_session_average_flow", 0.0),
            "last_session_hot_water_pct": state_data.get("last_session_hot_water_pct", 0.0),
            # Also surface the unit currently in use
            "volume_unit": unit,
        }

        # Update state (rounded to 2 decimals)
        self._attr_native_value = round(float(current_volume), 2)
        self._attr_available = True

        # Only write state if hass is available
        if self.hass is not None:
            self.async_write_ha_state()


class _BaseDependentSensor(SensorEntity):
    """Base for sensors that depend on upstream entities and tracker callbacks."""

    _tracked_entities: list[str]

    def __init__(self, entry: ConfigEntry, name: str, unique_suffix: str, tracked_entities: list[str]):
        self._entry = entry
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{unique_suffix}"
        self._attr_available = True
        self._attr_extra_state_attributes = {}
        # Track upstream to manage availability
        self._tracked_entities = [e for e in tracked_entities if e]

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

    def _upstream_available(self) -> bool:
        if not self._tracked_entities:
            return True
        for ent_id in self._tracked_entities:
            st = self.hass.states.get(ent_id) if self.hass else None
            if not st or st.state in (None, "unknown", "unavailable"):
                return False
        return True

    async def async_added_to_hass(self) -> None:
        # Initial availability check
        self._attr_available = self._upstream_available()
        self.async_write_ha_state()

        # Subscribe to upstream entity state changes to reflect availability
        if self._tracked_entities:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass,
                    self._tracked_entities,
                    self._async_upstream_changed,
                )
            )

    async def _async_upstream_changed(self, event) -> None:
        was = self._attr_available
        now = self._upstream_available()
        if was != now:
            self._attr_available = now
            self.async_write_ha_state()


class LastSessionDurationSensor(_BaseDependentSensor):
    """Reports the duration (s) of the last completed session."""

    _attr_icon = "mdi:timer"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, entry: ConfigEntry, name: str, unique_suffix: str, tracked_entities: list[str]):
        super().__init__(entry, name, unique_suffix, tracked_entities)
        self._attr_native_unit_of_measurement = "s"
        self._attr_native_value = 0

    async def update_from_tracker(self, state_data: dict):
        # Only mark available True if upstream is available
        self._attr_available = self._upstream_available()
        val = int(state_data.get("last_session_duration", 0) or 0)
        self._attr_native_value = val
        self._attr_extra_state_attributes = {
            "debug_state": state_data.get("debug_state"),
        }
        if self.hass is not None:
            self.async_write_ha_state()


class LastSessionAverageFlowSensor(_BaseDependentSensor):
    """Reports average flow of the last session (volume unit per minute)."""

    _attr_icon = "mdi:water"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 2

    def __init__(self, entry: ConfigEntry, name: str, unique_suffix: str, tracked_entities: list[str]):
        super().__init__(entry, name, unique_suffix, tracked_entities)
        self._attr_native_unit_of_measurement = None  # set from volume unit
        self._attr_native_value = 0.0

    async def update_from_tracker(self, state_data: dict):
        self._attr_available = self._upstream_available()
        vol_unit = state_data.get("volume_unit")
        if vol_unit:
            self._attr_native_unit_of_measurement = f"{vol_unit}/min"
        val = float(state_data.get("last_session_average_flow", 0.0) or 0.0)
        self._attr_native_value = round(val, 2)
        self._attr_extra_state_attributes = {
            "volume_unit": vol_unit,
            "debug_state": state_data.get("debug_state"),
        }
        if self.hass is not None:
            self.async_write_ha_state()


class LastSessionHotWaterPctSensor(_BaseDependentSensor):
    """Reports hot water percentage of the last session."""

    _attr_icon = "mdi:fire"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, entry: ConfigEntry, name: str, unique_suffix: str, tracked_entities: list[str]):
        super().__init__(entry, name, unique_suffix, tracked_entities)
        self._attr_native_unit_of_measurement = "%"
        self._attr_native_value = 0.0

    async def update_from_tracker(self, state_data: dict):
        self._attr_available = self._upstream_available()
        val = float(state_data.get("last_session_hot_water_pct", 0.0) or 0.0)
        # Tracker rounds to 0.1; keep one decimal
        self._attr_native_value = round(val, 1)
        self._attr_extra_state_attributes = {
            "debug_state": state_data.get("debug_state"),
        }
        if self.hass is not None:
            self.async_write_ha_state()