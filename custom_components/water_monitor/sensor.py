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
from homeassistant.helpers import entity_registry as er

from .const import (
    DOMAIN,
    CONF_FLOW_SENSOR,
    CONF_VOLUME_SENSOR,
    CONF_HOT_WATER_SENSOR,
    CONF_MIN_SESSION_VOLUME,
    CONF_MIN_SESSION_DURATION,
    CONF_SESSION_GAP_TOLERANCE,
    CONF_SENSOR_PREFIX,
    UPDATE_INTERVAL,
    CONF_SESSIONS_USE_BASELINE_AS_ZERO,
    CONF_SESSIONS_IDLE_TO_CLOSE_S,
    CONF_SYNTHETIC_ENABLE,
    CONF_INCLUDE_SYNTHETIC_IN_DETECTORS,
    CONF_CALC_VOLUME_FROM_FLOW,
    CONF_INTEGRATION_METHOD,
    INTEGRATION_METHOD_TRAPEZOIDAL,
    INTEGRATION_METHOD_LEFT,
)
from .const import tracker_signal
from homeassistant.helpers.dispatcher import async_dispatcher_send
from .water_session_tracker import WaterSessionTracker
from .engine import WaterMonitorEngine

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
    sessions_use_baseline_as_zero = bool(_get(CONF_SESSIONS_USE_BASELINE_AS_ZERO, True))
    sessions_idle_to_close_s = int(_get(CONF_SESSIONS_IDLE_TO_CLOSE_S, 10))
    synthetic_master = bool(_get(CONF_SYNTHETIC_ENABLE, False))
    include_synth_in_detectors = bool(_get(CONF_INCLUDE_SYNTHETIC_IN_DETECTORS, False)) and synthetic_master
    sensor_prefix = _get(CONF_SENSOR_PREFIX, config_entry.title or "Water Monitor")
    # Auto-enable integration-from-flow if no volume sensor configured
    calc_volume_from_flow = bool(_get(CONF_CALC_VOLUME_FROM_FLOW, False)) or (not volume_sensor)
    integration_method = str(_get(CONF_INTEGRATION_METHOD, INTEGRATION_METHOD_TRAPEZOIDAL))

    main_sensor = WaterSessionSensor(
        entry=config_entry,
        flow_sensor=flow_sensor,
        volume_sensor=volume_sensor,
        hot_water_sensor=hot_water_sensor,
        min_session_volume=min_session_volume,
        min_session_duration=min_session_duration,
        session_gap_tolerance=session_gap_tolerance,
    sessions_use_baseline_as_zero=sessions_use_baseline_as_zero,
    sessions_idle_to_close_s=sessions_idle_to_close_s,
    include_synth_in_detectors=include_synth_in_detectors,
    include_synth_in_engine=synthetic_master,
    calc_volume_from_flow=calc_volume_from_flow,
    integration_method=integration_method,
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
    # Track only upstreams that actually drive updates; if integrating from flow, omit volume sensor
    tracked = [e for e in [flow_sensor, (volume_sensor if not calc_volume_from_flow else None), hot_water_sensor] if e]
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

    # Also forward to engine for persistence/analysis
    async def _forward_to_engine(state: dict):
        try:
            data = hass.data.get(DOMAIN, {}).get(config_entry.entry_id)
            engine: WaterMonitorEngine | None = data.get("engine") if data else None
            if engine:
                await engine.ingest_state(state)
            # Broadcast live tracker state for other entities (e.g., intelligent leak)
            async_dispatcher_send(hass, tracker_signal(config_entry.entry_id), state)
        except Exception:
            pass

    main_sensor.add_state_listener(_forward_to_engine)

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
        sessions_use_baseline_as_zero: bool,
        sessions_idle_to_close_s: int,
        include_synth_in_detectors: bool,
        include_synth_in_engine: bool,
        calc_volume_from_flow: bool,
        integration_method: str,
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
        )

        # Session boundary tuning
        self._sessions_use_baseline_as_zero = bool(sessions_use_baseline_as_zero)
        self._sessions_idle_to_close_s = int(sessions_idle_to_close_s)
        self._baseline_entity_id = None
        self._baseline_zero_started_at = None

        # Synthetic handling
        self._include_synth_in_detectors = bool(include_synth_in_detectors)
        self._include_synth_in_engine = bool(include_synth_in_engine)
        # Accumulator to integrate synthetic flow into volume for session metrics when enabled
        self._synthetic_volume_added = 0.0
        self._last_synth_update = None
        self._prev_synth_flow = 0.0
        self._prev_session_active = False
        self._last_session_synth_volume = 0.0

        # Optional: compute volume by integrating flow (per-session)
        self._calc_volume_from_flow = bool(calc_volume_from_flow)
        self._integration_method = integration_method or INTEGRATION_METHOD_TRAPEZOIDAL
        self._integrated_session_volume = 0.0
        self._last_integration_ts = None
        self._prev_flow_for_integration = 0.0

        # Track entities we're listening to
        if self._calc_volume_from_flow:
            # Only flow (+ hot water if provided)
            self._tracked_entities = [e for e in [self._flow_sensor, self._hot_water_sensor] if e]
        else:
            self._tracked_entities = (
                [self._flow_sensor, self._volume_sensor, self._hot_water_sensor]
                if self._hot_water_sensor
                else [self._flow_sensor, self._volume_sensor]
            )

        # Periodic update tracking
        self._periodic_update_unsub = None
        self._periodic_interval_s = None  # seconds, or None

        # Callbacks for dependent sensors (current session + metrics sensors)
        self._listeners = []
        # Entities we may subscribe to for immediate updates
        self._synthetic_entity_id = None

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

        # Resolve the Expected Baseline number entity_id by unique_id
        try:
            reg = er.async_get(self.hass)
            target_uid = f"{self._entry.entry_id}_expected_baseline"
            for ent in er.async_entries_for_config_entry(reg, self._entry.entry_id):
                if ent.unique_id == target_uid and ent.domain == "number":
                    self._baseline_entity_id = ent.entity_id
                    break
        except Exception:  # registry may not be ready in unit tests
            self._baseline_entity_id = None

        # Resolve Synthetic Flow number entity id (for instant updates when changed)
        try:
            reg = er.async_get(self.hass)
            synth_uid = f"{self._entry.entry_id}_synthetic_flow_gpm"
            for ent in er.async_entries_for_config_entry(reg, self._entry.entry_id):
                if ent.unique_id == synth_uid and ent.domain == "number":
                    self._synthetic_entity_id = ent.entity_id
                    break
        except Exception:
            self._synthetic_entity_id = None

        # Track flow/volume/hot-water changes
        if self._tracked_entities:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass,
                    self._tracked_entities,
                    self._async_sensor_changed,
                )
            )
        # Track synthetic number changes (immediate refresh)
        if self._synthetic_entity_id:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass,
                    [self._synthetic_entity_id],
                    self._async_sensor_changed,
                )
            )

        # Defer the initial update so other entities finish being added
        self.hass.async_create_task(self._async_update_from_sensors())

    async def _async_sensor_changed(self, event) -> None:
        """Handle sensor state changes."""
        await self._async_update_from_sensors()

    def _ensure_timer(self, interval_s: Optional[int], reason: str):
        """Ensure a periodic timer is running at the desired interval; cancel if None."""
        # If no interval requested, cancel any existing timer
        if interval_s is None or interval_s <= 0:
            if self._periodic_update_unsub is not None:
                _LOGGER.debug("Stopping periodic updates: %s", reason)
                self._periodic_update_unsub()
                self._periodic_update_unsub = None
                self._periodic_interval_s = None
            return
        # If the same interval is already active, nothing to do
        if self._periodic_update_unsub is not None and self._periodic_interval_s == int(interval_s):
            return
        # Otherwise, (re)start timer at new interval
        if self._periodic_update_unsub is not None:
            try:
                self._periodic_update_unsub()
            except Exception:
                pass
            self._periodic_update_unsub = None
        self._periodic_interval_s = int(interval_s)
        _LOGGER.debug("Starting periodic updates every %ss: %s", self._periodic_interval_s, reason)
        self._periodic_update_unsub = async_track_time_interval(
            self.hass, self._async_periodic_update, timedelta(seconds=self._periodic_interval_s)
        )

    def _cancel_periodic_updates(self, reason: str):
        self._ensure_timer(None, reason)

    @callback
    async def _async_periodic_update(self, now: datetime) -> None:
        """Handle periodic updates for gap monitoring and session continuation."""
        await self._async_update_from_sensors()

    def _apply_cadence(self, state_data: dict) -> None:
        """Adaptive cadence: 5s during flow; 1s while gap at zero; none when idle."""
        session_active = bool(state_data.get("current_session_active", False))
        gap_active = bool(state_data.get("gap_active", False))
        flow_used = float(state_data.get("flow_used_by_engine", 0.0))
        if flow_used > 0.0:
            # Predictable UI during active water usage
            self._ensure_timer(5, "active session timing")
        elif gap_active and flow_used == 0.0:
            # Tight loop to promptly finalize gaps even while session remains active
            self._ensure_timer(1, "gap monitoring at zero flow")
        else:
            # Idle: cancel periodic updates (event-driven only)
            self._ensure_timer(None, "idle - event driven")

    async def _async_update_from_sensors(self) -> None:
        """Update the sensor from tracked entities."""
        try:
            # Get current sensor states
            flow_state = self.hass.states.get(self._flow_sensor)
            volume_state = self.hass.states.get(self._volume_sensor) if (self._volume_sensor and not self._calc_volume_from_flow) else None
            hot_water_state = self.hass.states.get(self._hot_water_sensor) if self._hot_water_sensor else None

            # Check if required sensors are available and ready
            if self._calc_volume_from_flow:
                # When integrating from flow, allow synthetic-only by treating missing/unknown flow as 0.0
                pass
            else:
                if not flow_state or not volume_state:
                    return
                if flow_state.state in (None, "unknown", "unavailable") or volume_state.state in (None, "unknown", "unavailable"):
                    return
            if hot_water_state and hot_water_state.state in (None, "unknown", "unavailable"):
                hot_water_state = None

            # Parse sensor values
            if flow_state and flow_state.state not in (None, "unknown", "unavailable"):
                try:
                    flow_rate = float(flow_state.state)
                except Exception:
                    flow_rate = 0.0
            else:
                # Synthetic-only or unavailable flow sensor
                flow_rate = 0.0
            volume_total = float(volume_state.state) if (volume_state and not self._calc_volume_from_flow) else 0.0
            hot_water_active = False
            if hot_water_state:
                s = str(hot_water_state.state).lower()
                hot_water_active = s in ("on", "true", "1")

            current_time = datetime.now(timezone.utc)

            # Determine unit from the volume sensor (if used), else infer from flow
            volume_unit = volume_state.attributes.get("unit_of_measurement") if volume_state else None
            flow_unit = (flow_state.attributes.get("unit_of_measurement") or "") if flow_state else ""
            if not volume_unit:
                fu = str(flow_unit).lower().replace(" ", "")
                # Check for gallons/minute first to avoid 'gal/min' matching 'l/min'
                if "gal/min" in fu or "gpm" in fu or "galpermin" in fu:
                    volume_unit = "gal"
                elif "l/min" in fu or "lpm" in fu or "lpermin" in fu:
                    volume_unit = "L"
            # Fallback for synthetic-only mode (synthetic uses gpm)
            if not volume_unit and self._calc_volume_from_flow and self._include_synth_in_engine:
                volume_unit = "gal"
            if volume_unit:
                self._attr_native_unit_of_measurement = volume_unit

            # Synthetic flow from integration-owned number (domain data)
            synthetic = 0.0
            try:
                dd = self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id)
                if isinstance(dd, dict):
                    sv = dd.get("synthetic_flow_gpm")
                    if isinstance(sv, (int, float)):
                        synthetic = max(0.0, float(sv))
            except Exception:
                pass

            # Base effective flow (before synthetic and baseline zeroing)
            effective_flow = flow_rate

            # Optionally treat near-baseline as zero with hysteresis
            if self._sessions_use_baseline_as_zero:
                baseline_val: float = 0.0
                if self._baseline_entity_id:
                    st = self.hass.states.get(self._baseline_entity_id)
                    if st and st.state not in (None, "unknown", "unavailable"):
                        try:
                            baseline_val = max(0.0, float(st.state))
                        except Exception:
                            baseline_val = 0.0
                if flow_rate <= baseline_val and baseline_val > 0.0:
                    if self._baseline_zero_started_at is None:
                        self._baseline_zero_started_at = current_time
                    elapsed = (current_time - self._baseline_zero_started_at).total_seconds()
                    if elapsed >= self._sessions_idle_to_close_s:
                        effective_flow = 0.0
                else:
                    self._baseline_zero_started_at = None

            # Apply synthetic according to flags
            engine_flow = effective_flow + (synthetic if self._include_synth_in_engine else 0.0)
            detectors_flow = effective_flow + (synthetic if self._include_synth_in_detectors else 0.0)

            will_start = engine_flow > 0.0 and not self._prev_session_active
            if self._include_synth_in_engine and will_start and not self._calc_volume_from_flow:
                self._synthetic_volume_added = 0.0
                self._last_synth_update = current_time

            adjusted_volume_total = volume_total

            # External volume path: optionally add synthetic volume
            if not self._calc_volume_from_flow:
                if self._include_synth_in_engine:
                    # Initialize on first use
                    if self._last_synth_update is None:
                        self._last_synth_update = current_time
                        self._prev_synth_flow = synthetic
                    # Integrate synthetic using selected method
                    dt = (current_time - self._last_synth_update).total_seconds()
                    if dt < 0:
                        dt = 0
                    if dt > 10:
                        dt = 10
                    if dt > 0:
                        if self._integration_method == INTEGRATION_METHOD_TRAPEZOIDAL:
                            dV = ((self._prev_synth_flow + synthetic) / 2.0) * (dt / 60.0)
                        else:
                            dV = (self._prev_synth_flow) * (dt / 60.0)
                        if dV > 0:
                            self._synthetic_volume_added += dV
                    # Advance state
                    self._last_synth_update = current_time
                    self._prev_synth_flow = synthetic
                    adjusted_volume_total = volume_total + max(0.0, self._synthetic_volume_added)
                else:
                    self._last_synth_update = None
                    self._prev_synth_flow = 0.0
                    self._synthetic_volume_added = 0.0
            else:
                # Flow integration path
                if will_start:
                    self._integrated_session_volume = 0.0
                    self._last_integration_ts = current_time
                    self._prev_flow_for_integration = engine_flow
                if self._last_integration_ts is None:
                    self._last_integration_ts = current_time
                    self._prev_flow_for_integration = engine_flow
                else:
                    dt = (current_time - self._last_integration_ts).total_seconds()
                    if dt < 0:
                        dt = 0
                    if dt > 10:
                        dt = 10
                    if self._integration_method == INTEGRATION_METHOD_TRAPEZOIDAL:
                        dV = ((self._prev_flow_for_integration + engine_flow) / 2.0) * (dt / 60.0)
                    else:
                        dV = (self._prev_flow_for_integration) * (dt / 60.0)
                    if dV > 0:
                        self._integrated_session_volume += max(0.0, dV)
                    self._last_integration_ts = current_time
                    self._prev_flow_for_integration = engine_flow
                adjusted_volume_total = max(0.0, self._integrated_session_volume)

            # Update tracker
            state_data = self._tracker.update(
                flow_rate=engine_flow,
                volume_total=adjusted_volume_total,
                hot_water_active=hot_water_active,
                timestamp=current_time,
            )

            # Session transitions
            current_active = bool(state_data.get("current_session_active", False))
            just_started = current_active and not self._prev_session_active
            just_ended = (not current_active) and self._prev_session_active

            if just_started:
                self._synthetic_volume_added = 0.0
                self._last_synth_update = current_time
                self._prev_synth_flow = synthetic
                if self._calc_volume_from_flow:
                    self._integrated_session_volume = 0.0
                    self._last_integration_ts = current_time
                    self._prev_flow_for_integration = engine_flow
            if just_ended:
                self._last_session_synth_volume = float(max(0.0, self._synthetic_volume_added))
                if self._calc_volume_from_flow:
                    self._last_integration_ts = None
                    self._prev_flow_for_integration = 0.0
                # Reset synthetic integrator timing until next session
                self._last_synth_update = None
                self._prev_synth_flow = 0.0

            # Enrich state for listeners
            state_data["volume_unit"] = volume_unit
            state_data["flow_unit"] = flow_unit
            state_data["flow_base"] = float(flow_rate)
            state_data["synthetic_flow_gpm"] = float(synthetic)
            state_data["flow_used_by_engine"] = float(engine_flow)
            state_data["detectors_flow"] = float(detectors_flow)
            state_data["flow_sensor_value"] = float(detectors_flow)
            state_data["synthetic_volume_added"] = float(self._synthetic_volume_added)
            state_data["_cad_gap_active_zero"] = bool(state_data.get("gap_active", False) and float(engine_flow) == 0.0)
            # Expose integration method and sampling cadence for transparency
            state_data["integration_method"] = (
                "external_volume_sensor" if not self._calc_volume_from_flow and self._volume_sensor else self._integration_method
            )
            state_data["sampling_active_seconds"] = 5
            state_data["sampling_gap_seconds"] = 1

            # Update this entity
            last_session_volume = float(state_data.get("last_session_volume", 0.0))
            self._attr_native_value = round(last_session_volume, 2)
            self._attr_extra_state_attributes = {
                "current_session_active": state_data.get("current_session_active", False),
                "gap_active": state_data.get("gap_active", False),
                "current_session_start": state_data.get("current_session_start"),
                "current_session_end": None if state_data.get("current_session_active", False) else state_data.get("last_session_end"),
                "original_session_start": state_data.get("original_session_start"),
                "current_session_volume": state_data.get("current_session_volume", 0.0),
                "current_session_duration": state_data.get("current_session_duration", 0),
                "current_session_average_flow": state_data.get("current_session_average_flow", 0.0),
                "current_session_hot_water_pct": state_data.get("current_session_hot_water_pct", 0.0),
                "last_session_start": state_data.get("last_session_start"),
                "last_session_end": state_data.get("last_session_end"),
                "last_session_volume": state_data.get("last_session_volume", 0.0),
                "last_session_duration": state_data.get("last_session_duration", 0),
                "last_session_average_flow": state_data.get("last_session_average_flow", 0.0),
                "last_session_hot_water_pct": state_data.get("last_session_hot_water_pct", 0.0),
                "last_session_gapped_sessions": state_data.get("last_session_gapped_sessions", 0),
                "flow_sensor_value": float(detectors_flow),
                "flow_base": float(flow_rate),
                "synthetic_flow_gpm": float(synthetic),
                "flow_used_by_engine": float(engine_flow),
                "detectors_flow": float(detectors_flow),
                "synthetic_volume_added": round(float(self._synthetic_volume_added), 3),
                "last_session_synthetic_volume": round(float(self._last_session_synth_volume), 3),
                "volume_unit": volume_unit,
                "flow_unit": flow_unit,
                "unit_of_measurement": volume_unit,
                "integration_method": state_data.get("integration_method"),
                "sampling_active_seconds": state_data.get("sampling_active_seconds"),
                "sampling_gap_seconds": state_data.get("sampling_gap_seconds"),
                "debug_state": state_data.get("debug_state", "UNKNOWN"),
            }
            self._attr_available = True

            # Notify all listeners (dependent sensors)
            for cb in list(self._listeners):
                try:
                    await cb(state_data)
                except Exception as err:
                    _LOGGER.exception("Listener update failed: %s", err)

            # Apply adaptive cadence based on state and write state
            self._apply_cadence(state_data)
            self.async_write_ha_state()
            self._prev_session_active = current_active

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
        """Return current session volume if active; otherwise 0."""
        current_volume = float(state_data.get("current_session_volume", 0.0) or 0.0)
        session_active = bool(state_data.get("current_session_active", False))
        return current_volume if session_active and current_volume > 0 else 0.0

    def _triage_stage(self, state_data: dict) -> str:
        """Which stage should drive attributes: current or final (no intermediate)."""
        if state_data.get("current_session_active", False) and state_data.get("current_session_volume", 0.0) > 0:
            return "current"
        return "final"

    async def update_from_tracker(self, state_data: dict):
        """Update this sensor when main sensor updates."""
        # Sync the unit from the main sensor (derived from source volume sensor)
        unit = state_data.get("volume_unit")
        if unit:
            self._attr_native_unit_of_measurement = unit

        current_volume = self._calculate_current_volume(state_data)
        stage = self._triage_stage(state_data)

        # Choose attributes based on stage (no intermediate fields)
        if stage == "current":
            session_duration = int(state_data.get("current_session_duration", 0))
            session_avg_flow = float(state_data.get("current_session_average_flow", 0.0))
            session_hot_pct = float(state_data.get("current_session_hot_water_pct", 0.0))
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
            # Session timestamps
            "current_session_start": state_data.get("current_session_start"),
            "current_session_end": None if state_data.get("current_session_active", False) else state_data.get("last_session_end"),
            "original_session_start": state_data.get("original_session_start"),
            "last_session_start": state_data.get("last_session_start"),
            "last_session_end": state_data.get("last_session_end"),
            # Instantaneous and debug
            "flow_sensor_value": state_data.get("flow_sensor_value", 0.0),
            "flow_base": state_data.get("flow_base", 0.0),
            "synthetic_flow_gpm": state_data.get("synthetic_flow_gpm", 0.0),
            "flow_used_by_engine": state_data.get("flow_used_by_engine", 0.0),
            "detectors_flow": state_data.get("detectors_flow", 0.0),
            "synthetic_volume_added": state_data.get("synthetic_volume_added", 0.0),
            # Raw current and last values (no intermediate)
            "current_session_volume": state_data.get("current_session_volume", 0.0),
            "current_session_duration": state_data.get("current_session_duration", 0),
            "current_session_average_flow": state_data.get("current_session_average_flow", 0.0),
            "current_session_hot_water_pct": state_data.get("current_session_hot_water_pct", 0.0),
            "last_session_volume": state_data.get("last_session_volume", 0.0),
            "last_session_duration": state_data.get("last_session_duration", 0),
            "last_session_average_flow": state_data.get("last_session_average_flow", 0.0),
            "last_session_hot_water_pct": state_data.get("last_session_hot_water_pct", 0.0),
            # Units
            "volume_unit": unit,
            "flow_unit": state_data.get("flow_unit"),
            "unit_of_measurement": unit,
            # Transparency
            "integration_method": state_data.get("integration_method"),
            "sampling_active_seconds": state_data.get("sampling_active_seconds"),
            "sampling_gap_seconds": state_data.get("sampling_gap_seconds"),
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
            "integration_method": state_data.get("integration_method"),
            "sampling_active_seconds": state_data.get("sampling_active_seconds"),
            "sampling_gap_seconds": state_data.get("sampling_gap_seconds"),
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
        flow_unit = state_data.get("flow_unit")
        # If flow_unit wasn't propagated, resolve specifically from the configured flow sensor
        if not flow_unit and getattr(self, "_tracked_entities", None):
            flow_ent = None
            for ent_id in self._tracked_entities:
                if ent_id and ent_id.startswith("sensor."):
                    # Prefer entities with typical flow units in attributes
                    st = self.hass.states.get(ent_id) if self.hass else None
                    if st:
                        u = (st.attributes.get("unit_of_measurement") or "").lower()
                        if any(x in u for x in ["/min", "/s", "/h", "gpm", "lpm", "l/min", "kw", "w"]):
                            flow_ent = ent_id
                            flow_unit = st.attributes.get("unit_of_measurement")
                            break

        # Compute average using last session volume/duration and target flow unit when possible
        volume = float(state_data.get("last_session_volume", 0.0) or 0.0)
        duration_s = int(state_data.get("last_session_duration", 0) or 0)

        def compute_avg(volume_val: float, dur_s: int, f_unit: Optional[str], v_unit: Optional[str]) -> tuple[float, Optional[str]]:
            if dur_s <= 0:
                # No duration; default to 0 with unit preference
                if f_unit:
                    return 0.0, f_unit
                if v_unit:
                    return 0.0, f"{v_unit}/min"
                return 0.0, None
            if f_unit:
                u = f_unit.strip().lower()
                # Electricity: kW or W (energy is kWh)
                if u == "kw":
                    # kW = kWh / hours
                    val_kw = (volume_val) / (dur_s / 3600.0)
                    return val_kw, "kW"
                if u == "w":
                    val_kw = (volume_val) / (dur_s / 3600.0)
                    return val_kw * 1000.0, "W"
                # Common water flow units
                if "/s" in u:
                    # e.g., L/s, gal/s, m³/s
                    return (volume_val / dur_s), f_unit
                if "/min" in u:
                    # e.g., L/min, gal/min
                    return (volume_val / dur_s) * 60.0, f_unit
                if "/h" in u or "/hr" in u or "/hour" in u:
                    # e.g., m³/h
                    return (volume_val / dur_s) * 3600.0, f_unit
                # Fallback: use volume/min convention
                if v_unit:
                    return (volume_val / dur_s) * 60.0, f"{v_unit}/min"
                return (volume_val / dur_s) * 60.0, None
            # No flow unit available; fall back to volume per minute
            if v_unit:
                return (volume_val / dur_s) * 60.0, f"{v_unit}/min"
            return (volume_val / dur_s) * 60.0, None

        value, unit = compute_avg(volume, duration_s, flow_unit, vol_unit)
        self._attr_native_value = round(float(value or 0.0), 2)
        self._attr_native_unit_of_measurement = unit
        self._attr_extra_state_attributes = {
            "volume_unit": vol_unit,
            "flow_unit": flow_unit,
            "debug_state": state_data.get("debug_state"),
            "integration_method": state_data.get("integration_method"),
            "sampling_active_seconds": state_data.get("sampling_active_seconds"),
            "sampling_gap_seconds": state_data.get("sampling_gap_seconds"),
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
            "integration_method": state_data.get("integration_method"),
            "sampling_active_seconds": state_data.get("sampling_active_seconds"),
            "sampling_gap_seconds": state_data.get("sampling_gap_seconds"),
        }
        if self.hass is not None:
            self.async_write_ha_state()