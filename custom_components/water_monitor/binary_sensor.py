"""Water Monitor binary sensors.

Phase 1: Upstream health binary sensor.
Phase 2: Tank refill leak binary sensor (event-driven via last_session sensor).
"""
from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Optional, Deque, Tuple

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_call_later,
    async_track_time_interval,
)
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import (
    DOMAIN,
    CONF_SENSOR_PREFIX,
    CONF_FLOW_SENSOR,
    CONF_VOLUME_SENSOR,
    CONF_HOT_WATER_SENSOR,
    # Low-flow
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
    UPDATE_INTERVAL,
    # Tank leak
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
from .const import engine_signal, tracker_signal
from .engine import WaterMonitorEngine

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Water Monitor binary sensors from a config entry."""
    opts = {**entry.data, **entry.options}
    prefix = opts.get(CONF_SENSOR_PREFIX) or "Water Monitor"
    flow_sensor = opts.get(CONF_FLOW_SENSOR)
    volume_sensor = opts.get(CONF_VOLUME_SENSOR)
    hot_water_sensor = opts.get(CONF_HOT_WATER_SENSOR)

    entities: list[BinarySensorEntity] = []

    # Upstream health sensor
    entities.append(
        UpstreamHealthBinarySensor(
            entry=entry,
            name=f"{prefix} Upstream sensors health",
            flow_entity_id=flow_sensor,
            volume_entity_id=volume_sensor,
            hot_water_entity_id=hot_water_sensor,
        )
    )

    # Low-flow leak detector (optional)
    if opts.get(CONF_LOW_FLOW_ENABLE):
        entities.append(
            LowFlowLeakBinarySensor(
                entry=entry,
                name=f"{prefix} Low-flow leak",
                max_low_flow=float(opts.get(CONF_LOW_FLOW_MAX_FLOW) or 0.5),
                seed_s=int(opts.get(CONF_LOW_FLOW_SEED_S) or 60),
                min_s=int(opts.get(CONF_LOW_FLOW_MIN_S) or 300),
                clear_idle_s=int(opts.get(CONF_LOW_FLOW_CLEAR_IDLE_S) or 30),
                counting_mode=str(opts.get(CONF_LOW_FLOW_COUNTING_MODE) or COUNTING_MODE_NONZERO),
                smoothing_s=int(opts.get(CONF_LOW_FLOW_SMOOTHING_S) or 0),
                cooldown_s=int(opts.get(CONF_LOW_FLOW_COOLDOWN_S) or 0),
                clear_on_high_s=(
                    int(opts.get(CONF_LOW_FLOW_CLEAR_ON_HIGH_S))
                    if opts.get(CONF_LOW_FLOW_CLEAR_ON_HIGH_S) not in (None, "")
                    else None
                ),
                baseline_margin_pct=float(opts.get(CONF_LOW_FLOW_BASELINE_MARGIN_PCT) or 10.0),
                flow_entity_id=flow_sensor,
            )
        )

    # Tank refill leak detector (optional)
    if opts.get(CONF_TANK_LEAK_ENABLE):
        entities.append(
            TankRefillLeakBinarySensor(
                entry=entry,
                name=f"{prefix} Tank refill leak",
                min_volume=float(opts.get(CONF_TANK_LEAK_MIN_REFILL_VOLUME) or 0.0),
                max_volume=float(opts.get(CONF_TANK_LEAK_MAX_REFILL_VOLUME) or 0.0),
                tol_pct=float(opts.get(CONF_TANK_LEAK_TOLERANCE_PCT) or 10.0),
                repeat=int(opts.get(CONF_TANK_LEAK_REPEAT_COUNT) or 3),
                window_s=int(opts.get(CONF_TANK_LEAK_WINDOW_S) or 15 * 60),
                clear_idle_s=int(opts.get(CONF_TANK_LEAK_CLEAR_IDLE_S) or 30 * 60),
                cooldown_s=int(opts.get(CONF_TANK_LEAK_COOLDOWN_S) or 0),
                min_duration_s=int(opts.get(CONF_TANK_LEAK_MIN_REFILL_DURATION_S) or 0),
                max_duration_s=int(opts.get(CONF_TANK_LEAK_MAX_REFILL_DURATION_S) or 0),
            )
        )

    # Engine status binary sensor (reflects data collection and anomaly)
    entities.append(
        EngineStatusBinarySensor(
            entry=entry,
            name=f"{prefix} Daily analysis status",
        )
    )

    # Intelligent leak detector (simple baseline version)
    entities.append(
        IntelligentLeakBinarySensor(
            entry=entry,
            name=f"{prefix} Intelligent leak",
        )
    )

    async_add_entities(entities)


class EngineStatusBinarySensor(BinarySensorEntity):
    """Shows whether the engine has flagged an anomaly for the latest daily summary.

    Attributes also include last session/summary timestamps and counts for visibility.
    """

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, entry: ConfigEntry, name: str) -> None:
        self._entry = entry
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_engine_status"
        self._attr_is_on = False
        self._attr_available = True
        self._attr_extra_state_attributes = {}
        self._unsub = None

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
        # Subscribe to engine dispatches
        sig = engine_signal(self._entry.entry_id)
        self._unsub = async_dispatcher_connect(self.hass, sig, self._on_engine_event)
        self._attr_available = True
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None
        await super().async_will_remove_from_hass()

    @callback
    def _on_engine_event(self, payload: dict) -> None:
        """Process engine events to reflect status and anomaly flag."""
        try:
            typ = payload.get("type")
            if typ == "ingest":
                rec = payload.get("record", {})
                self._attr_extra_state_attributes.update({
                    "last_session_ended_at": rec.get("ended_at"),
                    "last_session_volume": rec.get("volume"),
                    "last_session_duration_s": rec.get("duration_s"),
                })
                # Data collection active
                self._attr_available = True
            elif typ == "daily":
                summary = payload.get("summary", {})
                anomaly = bool(summary.get("anomaly", False))
                self._attr_is_on = anomaly
                self._attr_extra_state_attributes.update({
                    "last_daily_date": summary.get("date"),
                    "last_daily_total_volume": summary.get("total_volume"),
                    "last_daily_sessions": summary.get("sessions"),
                    "baseline_mean": summary.get("baseline_mean"),
                    "baseline_std": summary.get("baseline_std"),
                    "threshold_3sigma": summary.get("threshold_3sigma"),
                })
            # Write updated state
            self.async_write_ha_state()
        except Exception:
            # Defensive: don’t throw from callback
            pass


class IntelligentLeakBinarySensor(BinarySensorEntity):
    """Real-time leak detection based on current-session and simple hourly baselines."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, entry: ConfigEntry, name: str) -> None:
        self._entry = entry
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_intelligent_leak"
        self._attr_is_on = False
        self._attr_available = True
        self._attr_extra_state_attributes = {}
        self._unsub = None
        # Last evaluation timestamp; set during tracker callbacks
        self._last_eval_ts = None
        self._sensitivity_entity_id = None
        # Track wall-clock flow activity in case sessions are suppressed by baseline-as-zero
        self._flow_active_start = None
        self._last_flow_now = 0.0

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
        # Subscribe to live tracker updates
        sig = tracker_signal(self._entry.entry_id)
        self._unsub = async_dispatcher_connect(self.hass, sig, self._on_tracker_update)
        self._attr_available = True
        # Resolve the Leak alert sensitivity number entity_id by unique_id
        try:
            reg = er.async_get(self.hass)
            target_uid = f"{self._entry.entry_id}_leak_sensitivity"
            for ent in er.async_entries_for_config_entry(reg, self._entry.entry_id):
                if ent.unique_id == target_uid and ent.domain == "number":
                    self._sensitivity_entity_id = ent.entity_id
                    break
        except Exception:
            self._sensitivity_entity_id = None
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None
        await super().async_will_remove_from_hass()

    def _get_engine(self) -> Optional[WaterMonitorEngine]:
        try:
            data = self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id)
            return data.get("engine") if isinstance(data, dict) else None
        except Exception:
            return None

    def _hour_and_daytype(self, now: datetime) -> tuple[int, str]:
        local = now.astimezone()
        return local.hour, ("weekend" if local.weekday() >= 5 else "weekday")

    def _get_sensitivity(self) -> float:
        """Read user sensitivity (0-100), default 50 when unavailable."""
        try:
            if self._sensitivity_entity_id:
                st = self.hass.states.get(self._sensitivity_entity_id)
                if st and st.state not in (None, "unknown", "unavailable"):
                    val = float(st.state)
                    return max(0.0, min(100.0, val))
        except Exception:
            pass
        return 50.0

    def _percentile_from_sensitivity(self, s: float) -> float:
        """Map 0..100 to 99..90 (linear), with 50 -> ~95."""
        s = max(0.0, min(100.0, s))
        return 99.0 - 0.09 * s

    def _interpolate_threshold(self, p90: float, p95: float, p99: float, target_p: float) -> float:
        target_p = max(90.0, min(99.0, target_p))
        if target_p <= 95.0:
            # Map 90..95 onto p90..p95
            t = (target_p - 90.0) / 5.0
            return p90 + t * max(0.0, p95 - p90)
        else:
            # Map 95..99 onto p95..p99
            t = (target_p - 95.0) / 4.0
            return p95 + t * max(0.0, p99 - p95)

    @callback
    def _on_tracker_update(self, state: dict) -> None:
        try:
            now = datetime.now(timezone.utc)
            self._last_eval_ts = now
            # Pull live session metrics
            active = bool(state.get("current_session_active", False))
            elapsed = int(state.get("current_session_duration", 0) or 0)
            avg_flow = float(state.get("current_session_average_flow", 0.0) or 0.0)
            hot_pct = float(state.get("current_session_hot_water_pct", 0.0) or 0.0)
            flow_now = float(state.get("flow_sensor_value", 0.0) or 0.0)
            # Maintain independent wall-clock elapsed while flow > 0
            if flow_now > 0.0:
                if self._flow_active_start is None:
                    self._flow_active_start = now
            else:
                self._flow_active_start = None
            flow_elapsed = int((now - self._flow_active_start).total_seconds()) if self._flow_active_start else 0

            # Choose elapsed for risk: prefer session elapsed, else fall back to wall-clock under flow
            eff_elapsed = elapsed if (active and elapsed > 0) else flow_elapsed

            # Fetch context-aware baseline for current hour/day_type and occupancy/person context
            eng = self._get_engine()
            hour, day_type = self._hour_and_daytype(now)
            stats = None
            if eng:
                # Ask engine for context-aware stats reflecting now
                stats = eng.get_context_stats_for_now()
            stats = stats or {
                "bucket": None,
                "count": 0,
                "p50": 0.0,
                "p90": 0.0,
                "p95": 0.0,
                "p99": 0.0,
                "level": 99,
            }
            # Compute chosen percentile from sensitivity
            sensitivity = self._get_sensitivity()
            chosen_p = self._percentile_from_sensitivity(sensitivity)
            p90 = float(stats.get("p90", 0.0) or 0.0)
            p95 = float(stats.get("p95", 0.0) or 0.0)
            p99 = float(stats.get("p99", 0.0) or 0.0)
            count = int(stats.get("count", 0) or 0)
            bucket = stats.get("bucket")
            occ_class = stats.get("occ_class") or "home"

            # Build effective threshold
            baseline_ready = count >= 10
            if baseline_ready and (p90 > 0.0 or p95 > 0.0 or p99 > 0.0):
                base_threshold = self._interpolate_threshold(p90, p95, p99, chosen_p)
            else:
                base_threshold = 0.0

            # Sparse-data fallback floor (minutes -> seconds)
            fallback_min_minutes = 45.0 - (25.0 * (sensitivity / 100.0))  # 45 at 0, ~32.5 at 50, 20 at 100
            fallback_floor_s = max(5.0 * 60.0, fallback_min_minutes * 60.0)

            # Apply policy floors/adjustments
            effective_threshold = base_threshold if base_threshold > 0 else fallback_floor_s
            if occ_class == "vacation":
                effective_threshold = max(effective_threshold, 45.0 * 60.0)
            elif occ_class == "away":
                effective_threshold = max(effective_threshold, 35.0 * 60.0)
            elif occ_class == "night":
                # tighten a bit at night but don’t go below 10 min to avoid over-aggression
                effective_threshold = max(effective_threshold * 0.85, 10.0 * 60.0)

            # Risk: scale by ratio to effective threshold
            reasons = []
            if eff_elapsed > 0 and effective_threshold > 0:
                risk = eff_elapsed / effective_threshold
                if baseline_ready:
                    reasons.append("elapsed>p{:.1f}".format(chosen_p))
                else:
                    reasons.append("fallback_floor")
            else:
                risk = 0.0

            # Slightly increase risk if flow is very low but persistent (drip leaks)
            if 0.0 < flow_now <= 0.3 and elapsed >= 10 * 60:
                risk += 0.3
                reasons.append("low_flow_persistent")

            is_on = risk >= 1.0
            prev = self._attr_is_on
            self._attr_is_on = is_on
            self._attr_extra_state_attributes = {
                "baseline_ready": baseline_ready,
                "bucket_used": bucket,
                "count": count,
                "p90": p90,
                "p95": p95,
                "p99": p99,
                "sensitivity_setting": sensitivity,
                "chosen_percentile": round(chosen_p, 1),
                "effective_threshold_s": round(effective_threshold, 1),
                "fallback_elapsed_floor_s": round(fallback_floor_s, 1),
                "policy_context": occ_class,
                "elapsed_s": eff_elapsed,
                "avg_flow": round(avg_flow, 2),
                "hot_pct": round(hot_pct, 1),
                "flow_now": flow_now,
                "risk": round(risk, 2),
                "reasons": reasons,
            }
            if prev != self._attr_is_on:
                self.async_write_ha_state()
            else:
                self.async_write_ha_state()
        except Exception:
            # Silent fail to avoid crashing dispatcher
            pass


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
        self._last_ok = {}

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


class LowFlowLeakBinarySensor(BinarySensorEntity):
    """Detects sustained low-flow conditions as a leak.

    Modes:
    - nonzero_wallclock: counts all wall-clock time while flow > 0
    - in_range_only: counts only while 0 < flow <= max_low_flow

    Baseline latch is accepted but treated like in_range_only for now.
    """

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(
        self,
        entry: ConfigEntry,
        name: str,
        max_low_flow: float,
        seed_s: int,
        min_s: int,
        clear_idle_s: int,
        counting_mode: str,
        smoothing_s: int,
        cooldown_s: int,
        clear_on_high_s: Optional[int],
        baseline_margin_pct: float,
        flow_entity_id: Optional[str],
    ) -> None:
        self._entry = entry
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_low_flow_leak"
        self._attr_is_on = False
        self._attr_available = True
        self._attr_extra_state_attributes = {}

        self._flow_entity_id = flow_entity_id

        self._max_low_flow = float(max_low_flow)
        self._seed_s = int(seed_s)
        self._min_s = int(min_s)
        self._clear_idle_s = int(clear_idle_s)
        self._counting_mode = counting_mode
        self._smoothing_s = int(smoothing_s)
        self._cooldown_s = int(cooldown_s)
        self._clear_on_high_s = int(clear_on_high_s) if clear_on_high_s else None
        self._baseline_margin_pct = float(baseline_margin_pct)

        self._unsub_state = None
        self._unsub_timer = None
        # Track detectors_flow provided by the tracker (includes synthetic when enabled)
        self._tracker_unsub = None
        self._last_detectors_flow = None

        # Runtime counters
        self._seeded = False
        self._seed_progress = 0.0
        self._count_progress = 0.0
        self._idle_zero_s = 0.0
        self._high_flow_s = 0.0
        self._last_update = None
        self._cooldown_until = None

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
        self._attr_is_on = False
        self._attr_available = True
        self._last_update = datetime.now(timezone.utc)
        self.async_write_ha_state()

        # Subscribe both to raw flow entity (for availability) and tracker for detectors flow
        if self._flow_entity_id:
            self._unsub_state = async_track_state_change_event(
                self.hass, [self._flow_entity_id], self._async_flow_changed
            )
        # Tracker subscription provides detectors_flow (includes synthetic when enabled)
        self._tracker_unsub = async_dispatcher_connect(
            self.hass, tracker_signal(self._entry.entry_id), self._on_tracker_update
        )
        # Periodic to advance clocks; start conservative (5s) until activity dictates
        self._unsub_timer = async_track_time_interval(
            self.hass, self._async_tick, timedelta(seconds=5)
        )
        self._tick_interval_s = 5
        self._recent_counting_hysteresis_s = 0.0

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_state:
            self._unsub_state()
            self._unsub_state = None
        if self._tracker_unsub:
            self._tracker_unsub()
            self._tracker_unsub = None
        if self._unsub_timer:
            self._unsub_timer()
            self._unsub_timer = None
        await super().async_will_remove_from_hass()

    @callback
    async def _async_flow_changed(self, event) -> None:
        await self._evaluate(datetime.now(timezone.utc))

    @callback
    async def _async_tick(self, now: datetime) -> None:
        await self._evaluate(now)

    def _current_flow(self) -> Optional[float]:
        # Prefer detectors flow from tracker when available; fall back to raw entity
        if self._last_detectors_flow is not None:
            try:
                return float(self._last_detectors_flow)
            except Exception:
                return 0.0
        st = self.hass.states.get(self._flow_entity_id) if self._flow_entity_id else None
        if not st or st.state in (None, "unknown", "unavailable"):
            return None
        try:
            return float(st.state)
        except (ValueError, TypeError):
            return None

    @callback
    def _on_tracker_update(self, state: dict) -> None:
        try:
            df = state.get("detectors_flow")
            if isinstance(df, (int, float)):
                self._last_detectors_flow = float(df)
        except Exception:
            pass

    async def _evaluate(self, now: datetime) -> None:
        if self._last_update is None:
            self._last_update = now
        dt = (now - self._last_update).total_seconds()
        if dt < 0:
            dt = 0
        flow = self._current_flow()
        if flow is None:
            # treat as zero flow for timers
            flow = 0.0

        # Counting activity by mode
        if self._counting_mode == COUNTING_MODE_NONZERO:
            counting_active = flow > 0.0
        else:  # IN_RANGE or BASELINE treated similarly for now
            counting_active = flow > 0.0 and (self._max_low_flow <= 0.0 or flow <= self._max_low_flow)

    # Seed and count progression
        if counting_active:
            # zero-idle resets while active
            self._idle_zero_s = 0.0
            if self._clear_on_high_s and self._max_low_flow > 0 and flow > self._max_low_flow:
                self._high_flow_s += dt
            else:
                self._high_flow_s = 0.0

            if not self._seeded:
                self._seed_progress += dt
                if self._seed_s == 0 or self._seed_progress >= self._seed_s:
                    self._seeded = True
                    self._count_progress = 0.0
            else:
                self._count_progress += dt
        else:
            # inactive flow
            if flow <= 0.0:
                self._idle_zero_s += dt
            else:
                self._idle_zero_s = 0.0
            if self._clear_on_high_s and self._max_low_flow > 0 and flow > self._max_low_flow:
                self._high_flow_s += dt
            else:
                self._high_flow_s = 0.0

            if not self._seeded:
                self._seed_progress = 0.0
            else:
                self._count_progress = 0.0

        # Adjust cadence with brief hysteresis
        desired = 1 if (counting_active or self._attr_is_on) else 5
        # Remember recent counting for 3s to avoid flapping
        if counting_active:
            self._recent_counting_hysteresis_s = 3.0
        else:
            if self._recent_counting_hysteresis_s > 0:
                self._recent_counting_hysteresis_s = max(0.0, self._recent_counting_hysteresis_s - dt)
                if self._recent_counting_hysteresis_s > 0:
                    desired = 1
        if desired != getattr(self, "_tick_interval_s", 5):
            # Resubscribe with new interval
            try:
                if self._unsub_timer:
                    self._unsub_timer()
            except Exception:
                pass
            self._tick_interval_s = desired
            self._unsub_timer = async_track_time_interval(
                self.hass, self._async_tick, timedelta(seconds=self._tick_interval_s)
            )

        # Clear conditions
        cleared = False
        if self._attr_is_on:
            if self._clear_idle_s > 0 and self._idle_zero_s >= self._clear_idle_s:
                self._attr_is_on = False
                cleared = True
            elif self._clear_on_high_s and self._high_flow_s >= self._clear_on_high_s:
                self._attr_is_on = False
                cleared = True
            if cleared and self._cooldown_s > 0:
                self._cooldown_until = now + timedelta(seconds=self._cooldown_s)

        # Trigger condition
        can_trigger = not self._cooldown_until or now >= self._cooldown_until
        if not self._attr_is_on and can_trigger and self._seeded and self._count_progress >= self._min_s:
            self._attr_is_on = True

        # Phase
        if self._attr_is_on:
            phase = "alarmed"
        elif not self._seeded:
            phase = "seeding" if counting_active else "idle"
        else:
            phase = "counting" if counting_active else "idle"

        self._attr_extra_state_attributes = {
            "mode": self._counting_mode,
            "phase": phase,
            "flow": flow,
            "max_low_flow": self._max_low_flow,
            "seed_required_s": self._seed_s,
            "seed_progress_s": round(self._seed_progress, 1),
            "min_duration_s": self._min_s,
            "count_progress_s": round(self._count_progress, 1),
            "idle_zero_s": round(self._idle_zero_s, 1),
            "high_flow_s": round(self._high_flow_s, 1),
            "clear_idle_s": self._clear_idle_s,
            "clear_on_high_s": self._clear_on_high_s,
            "cooldown_s": self._cooldown_s,
            "cooldown_until": self._cooldown_until.isoformat() if self._cooldown_until else None,
            "smoothing_s": self._smoothing_s,
            "baseline_margin_pct": self._baseline_margin_pct,
        }

        self._last_update = now
        self.async_write_ha_state()


class TankRefillLeakBinarySensor(BinarySensorEntity):
    """Detects repeating, similar-sized tank refills within a window.

    Event source: the integration's "last_session" sensor. Each time
    a last session completes, we read its volume and duration and treat it as a
    candidate refill event if it meets configured gates.
    """

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(
        self,
        entry: ConfigEntry,
        name: str,
        min_volume: float,
        max_volume: float,
        tol_pct: float,
        repeat: int,
        window_s: int,
        clear_idle_s: int,
        cooldown_s: int,
        min_duration_s: int,
        max_duration_s: int,
    ) -> None:
        self._entry = entry
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_tank_refill_leak"
        self._attr_is_on = False
        self._attr_available = True
        self._attr_extra_state_attributes = {}

        self._min_volume = float(min_volume)
        self._max_volume = float(max_volume)
        self._tol_pct = float(tol_pct)
        self._repeat = int(repeat)
        self._window_s = int(window_s)
        self._clear_idle_s = int(clear_idle_s)
        self._cooldown_s = int(cooldown_s)
        self._min_duration_s = int(min_duration_s)
        self._max_duration_s = int(max_duration_s)

        # Source entity (resolved by unique_id lookup)
        self._source_entity_id: Optional[str] = None
        self._unsub_state = None

        # Event memory (ts, volume, duration)
        self._history: Deque[Tuple[datetime, float, int]] = deque()
        self._last_event_ts: Optional[datetime] = None
        self._cooldown_until: Optional[datetime] = None
        self._last_seen_pair: Optional[Tuple[float, int]] = None

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
        self._attr_is_on = False
        self._attr_available = True
        self.async_write_ha_state()
        await self._resolve_and_subscribe()

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_state:
            self._unsub_state()
            self._unsub_state = None
        await super().async_will_remove_from_hass()

    async def _resolve_and_subscribe(self) -> None:
        """Find the last_session sensor entity_id and subscribe to its changes."""
        if self._source_entity_id:
            return
        ent_reg = er.async_get(self.hass)
        unique_id = f"{self._entry.entry_id}_last_session"
        entity = next(
            (e for e in ent_reg.entities.values() if e.platform == DOMAIN and e.unique_id == unique_id),
            None,
        )
        if entity is None:
            # Retry shortly; platform setup order can vary on first install
            async_call_later(self.hass, 2.0, lambda _: self.hass.async_create_task(self._resolve_and_subscribe()))
            return
        self._source_entity_id = entity.entity_id
        self._unsub_state = async_track_state_change_event(
            self.hass, [self._source_entity_id], self._async_source_changed
        )

    @callback
    async def _async_source_changed(self, event) -> None:
        await self._evaluate(datetime.now(timezone.utc))

    async def _evaluate(self, now: datetime) -> None:
        # Cooldown guard: don't re-trigger during cooldown
        if self._cooldown_until and now < self._cooldown_until:
            pass  # still update attributes/history, but won't set ON based on count

        if not self._source_entity_id:
            await self._resolve_and_subscribe()
            return

        main = self.hass.states.get(self._source_entity_id)
        if not main:
            return

        try:
            vol = float(main.attributes.get("last_session_volume", 0.0))
        except (ValueError, TypeError):
            vol = 0.0
        try:
            dur = int(main.attributes.get("last_session_duration", 0) or 0)
        except (ValueError, TypeError):
            dur = 0

        # Only record when the pair changes
        if self._last_seen_pair != (vol, dur):
            self._last_seen_pair = (vol, dur)
            # Apply duration gates (0 disables)
            duration_ok = True
            if self._min_duration_s > 0 and dur < self._min_duration_s:
                duration_ok = False
            if self._max_duration_s > 0 and dur > self._max_duration_s:
                duration_ok = False

            if duration_ok and vol >= self._min_volume and (
                self._max_volume <= 0.0 or vol <= self._max_volume
            ):
                self._history.append((now, vol, dur))
                self._last_event_ts = now

        # Purge outside window
        cutoff = now - timedelta(seconds=self._window_s)
        while self._history and self._history[0][0] < cutoff:
            self._history.popleft()

        # Count similar events to the latest within tolerance and collect contributors
        similar_count = 0
        contributing = []
        if self._history:
            latest_vol = self._history[-1][1]
            tol = latest_vol * (self._tol_pct / 100.0)
            lo, hi = latest_vol - tol, latest_vol + tol
            for ts, v, d in self._history:
                if lo <= v <= hi:
                    similar_count += 1
                    contributing.append({
                        "ts": ts.isoformat(),
                        "volume": v,
                        "duration_s": d,
                    })

        prev_on = self._attr_is_on
        can_trigger = not self._cooldown_until or now >= self._cooldown_until
        if can_trigger and similar_count >= self._repeat:
            self._attr_is_on = True
        else:
            # Auto-clear after idle period since last event
            if self._attr_is_on and self._last_event_ts and (now - self._last_event_ts).total_seconds() >= self._clear_idle_s:
                self._attr_is_on = False
                if self._cooldown_s > 0:
                    self._cooldown_until = now + timedelta(seconds=self._cooldown_s)

        self._attr_extra_state_attributes = {
            "events_in_window": len(self._history),
            "similar_count": similar_count,
            "min_refill_volume": self._min_volume,
            "max_refill_volume": self._max_volume,
            "tolerance_pct": self._tol_pct,
            "repeat_count": self._repeat,
            "window_s": self._window_s,
            "clear_idle_s": self._clear_idle_s,
            "cooldown_s": self._cooldown_s,
            "last_event": self._last_event_ts.isoformat() if self._last_event_ts else None,
            "min_refill_duration_s": self._min_duration_s,
            "max_refill_duration_s": self._max_duration_s,
            "contributing_events": contributing,
        }

        # Always write so attributes stay fresh
        if prev_on != self._attr_is_on:
            self.async_write_ha_state()
        else:
            self.async_write_ha_state()
