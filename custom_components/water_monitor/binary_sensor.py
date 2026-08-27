"""Water Monitor binary sensors.

Phase 1: Upstream health binary sensor.
Phase 2: Tank refill leak binary sensor (event-driven via last_session sensor).
"""
from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timedelta, timezone
from math import sqrt
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
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

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
    CONF_TANK_LEAK_MAX_HOT_WATER_PCT,
    # Intelligent leak detection
    CONF_INTEL_DETECT_ENABLE,
    CONF_INTEL_SUPPRESS_NOTIFICATIONS_DURING_LEARNING,
    CONF_INTEL_MINIMUM_LEARNING_DAYS,
    # Shutoff valve and auto-shutoff flags
    CONF_WATER_SHUTOFF_ENTITY,
    CONF_LOW_FLOW_AUTO_SHUTOFF,
    CONF_TANK_LEAK_AUTO_SHUTOFF,
    CONF_INTEL_AUTO_SHUTOFF,
)
from .const import engine_signal, tracker_signal
from .engine import WaterMonitorEngine, percentile_of

_LOGGER = logging.getLogger(__name__)


class LeakDetectorBase(BinarySensorEntity):
    """Base class for leak detectors with common valve operations."""
    
    def __init__(self, entry: ConfigEntry, name: str) -> None:
        super().__init__()
        self._entry = entry
        self._attr_name = name
    
    def _get_valve_context(self, auto_shutoff_config_key: str) -> tuple[Optional[str], bool, bool, bool]:
        """Return (valve_entity_id, valve_off, auto_shutoff_enabled, effective)."""
        ex = {**self._entry.data, **self._entry.options}
        valve = ex.get(CONF_WATER_SHUTOFF_ENTITY) or ""
        # Also check auto-discovered valve from domain data
        if not valve:
            try:
                data = self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id)
                if isinstance(data, dict):
                    valve = data.get("valve_entity_id") or ""
            except Exception:
                pass
        auto = bool(ex.get(auto_shutoff_config_key, False))
        data = self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id)
        valve_off = bool(data.get("valve_off", False)) if isinstance(data, dict) else False
        effective = bool(valve and auto)
        return (valve or None, valve_off, auto, effective)
    
    def _async_call_valve_off(self, valve_entity_id: str) -> None:
        try:
            domain = valve_entity_id.split(".")[0]
            if domain == "valve":
                srv_domain, service = "valve", "close_valve"
            elif domain in ("switch", "input_boolean"):
                srv_domain, service = domain, "turn_off"
            else:
                return
            self.hass.async_create_task(
                self.hass.services.async_call(srv_domain, service, {"entity_id": valve_entity_id}, blocking=False)
            )
        except Exception:
            pass


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Water Monitor binary sensors from a config entry."""
    opts = {**entry.data, **entry.options}
    prefix = opts.get(CONF_SENSOR_PREFIX) or "Water Monitor"
    flow_sensor = opts.get(CONF_FLOW_SENSOR)
    volume_sensor = opts.get(CONF_VOLUME_SENSOR)
    hot_water_sensor = opts.get(CONF_HOT_WATER_SENSOR)
    valve_sensor = opts.get(CONF_WATER_SHUTOFF_ENTITY)

    entities: list[BinarySensorEntity] = []

    # Upstream health sensor
    entities.append(
        UpstreamHealthBinarySensor(
            entry=entry,
            name=f"{prefix} Upstream sensors health",
            flow_entity_id=flow_sensor,
            volume_entity_id=volume_sensor,
            hot_water_entity_id=hot_water_sensor,
            valve_entity_id=valve_sensor,
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
                max_hot_water_pct=float(opts.get(CONF_TANK_LEAK_MAX_HOT_WATER_PCT) or 25.0),
            )
        )

    # Engine status binary sensor (reflects data collection and anomaly)
    entities.append(
        EngineStatusBinarySensor(
            entry=entry,
            name=f"{prefix} Daily analysis status",
        )
    )

    # Intelligent leak detector (optional)
    if opts.get(CONF_INTEL_DETECT_ENABLE):
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
            prev_on = self._attr_is_on
            prev_attrs = dict(self._attr_extra_state_attributes)
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
            # Write if the on/off state changed OR attributes changed (e.g. an
            # "ingest" payload only updates diagnostics, never _attr_is_on).
            if prev_on != self._attr_is_on or self._attr_extra_state_attributes != prev_attrs:
                self.async_write_ha_state()
        except Exception:
            # Defensive: don’t throw from callback
            pass


class IntelligentLeakBinarySensor(LeakDetectorBase):
    """Experimental intelligent leak detector using learned behavior profiles.

    The detector learns session-shape profiles over time (volume, duration,
    average flow, hot water percent) and evaluates the current live session
    against those profiles.

    Stages:
    - normal
    - potential (early warning)
    - confirmed (high confidence leak)
    """

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    # Minimum sustained zero-flow duration (seconds) required before a flow-based
    # instant-clear ("flow just stopped") is honored while in potential/confirmed
    # stage. Without this, a single momentary zero reading from a pulsing/
    # intermittent leak (e.g. a cycling toilet flapper) can clear the alarm, which
    # then immediately re-triggers on the next tick — causing rapid on/off flapping.
    _CLEAR_IDLE_S = 30.0

    def __init__(self, entry: ConfigEntry, name: str) -> None:
        super().__init__(entry, name)
        self._attr_unique_id = f"{entry.entry_id}_intelligent_leak"
        self._attr_is_on = False
        self._attr_available = True
        self._attr_extra_state_attributes = {}
        self._unsub = None
        self._sensitivity_entity_id = None
        # Cross-detector corroboration: entity ids for the other leak detectors,
        # resolved in async_added_to_hass(). None when that detector is disabled
        # or not found — corroboration is a boost, never a hard dependency.
        self._low_flow_entity_id: Optional[str] = None
        self._tank_refill_entity_id: Optional[str] = None
        # Sticky per-session flag: True once another detector confirms a leak
        # during the current session. Reset when the session ends.
        self._session_leak_corroborated = False
        self._corroboration_source: Optional[str] = None

        # Runtime session tracking
        self._last_eval_ts: Optional[datetime] = None
        self._flow_active_start = None
        self._last_flow_now = 0.0
        # Accumulated wall-clock seconds of continuous zero flow — used to debounce
        # flow-based instant-clear transitions (see _CLEAR_IDLE_S above).
        self._idle_zero_s = 0.0

        # Trigger metadata
        self._synthetic_flow_at_trigger = 0.0

        # Stage machine state
        self._leak_stage = "normal"
        self._potential_candidate_since: Optional[datetime] = None
        self._confirmed_candidate_since: Optional[datetime] = None

        # Learned profile model (experimental, in-memory)
        # profile id -> centroid/count
        self._profiles: dict[str, dict] = {}
        # profile id -> total learned count
        self._profile_total_count: dict[str, int] = {}
        # profile id -> context occurrence counts
        self._profile_context_count: dict[str, dict[str, int]] = {}
        self._learned_sessions = 0
        self._last_finalized_sig: Optional[tuple[float, int, float]] = None
        # Persistent profile store — survives HA restarts
        self._profile_store: Optional[Store] = None

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

        # Resolve the Leak alert sensitivity number entity_id by unique_id, and
        # (for cross-detector corroboration) the Low-Flow/Tank-Refill leak entity
        # ids. Any of these may legitimately be absent if that feature is disabled.
        try:
            reg = er.async_get(self.hass)
            target_uid = f"{self._entry.entry_id}_leak_sensitivity"
            low_flow_uid = f"{self._entry.entry_id}_low_flow_leak"
            tank_refill_uid = f"{self._entry.entry_id}_tank_refill_leak"
            for ent in er.async_entries_for_config_entry(reg, self._entry.entry_id):
                if ent.unique_id == target_uid and ent.domain == "number":
                    self._sensitivity_entity_id = ent.entity_id
                elif ent.unique_id == low_flow_uid and ent.domain == "binary_sensor":
                    self._low_flow_entity_id = ent.entity_id
                elif ent.unique_id == tank_refill_uid and ent.domain == "binary_sensor":
                    self._tank_refill_entity_id = ent.entity_id
        except Exception:
            self._sensitivity_entity_id = None
            self._low_flow_entity_id = None
            self._tank_refill_entity_id = None

        # Initialize persistent profile store for this entry.
        self._profile_store = Store(
            self.hass, 1, f"{DOMAIN}_{self._entry.entry_id}_intel_profiles.json"
        )

        # Bootstrap profiles from engine session history (order-dependent clustering).
        self._bootstrap_profiles_from_engine()
        bootstrap_count = self._learned_sessions

        # Load persisted profiles from storage — overrides bootstrap when the stored
        # state has more learned sessions, which means it has been refined by real
        # post-restart live learning and is more accurate than a fresh re-cluster.
        await self._load_profiles(bootstrap_count)

        # Publish a reference so the reset_intelligent_leak_learning service can find us.
        try:
            data = self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id)
            if isinstance(data, dict):
                data["intel_leak_entity"] = self
        except Exception:
            pass

        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None
        try:
            data = self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id)
            if isinstance(data, dict) and data.get("intel_leak_entity") is self:
                data.pop("intel_leak_entity", None)
        except Exception:
            pass
        await self._save_profiles()
        await super().async_will_remove_from_hass()

    async def _load_profiles(self, bootstrap_count: int) -> None:
        """Load persisted profile model from storage, replacing bootstrap when better."""
        if self._profile_store is None:
            return
        try:
            data = await self._profile_store.async_load()
            if not data or not isinstance(data, dict):
                return
            stored_count = int(data.get("learned_sessions", 0))
            # Only use stored data if it has MORE learned sessions than bootstrap.
            # Fewer sessions means the store is stale/corrupt — fall back to bootstrap.
            if stored_count <= bootstrap_count:
                return
            self._profiles = {str(k): dict(v) for k, v in data.get("profiles", {}).items()}
            self._profile_total_count = {str(k): int(v) for k, v in data.get("profile_total_count", {}).items()}
            raw_ctx = data.get("profile_context_count", {})
            self._profile_context_count = {
                str(pid): {str(ctx): int(n) for ctx, n in counts.items()}
                for pid, counts in raw_ctx.items()
            }
            self._learned_sessions = stored_count
        except Exception as e:
            _LOGGER.warning("Failed to load intelligent leak profiles: %s", e)

    async def _save_profiles(self) -> None:
        """Persist current profile model to storage."""
        # Note: deliberately do NOT skip saving when self._profiles is empty -
        # async_reset_learning() needs to be able to persist a cleared model
        # (e.g. when engine history is empty), otherwise a stale stored model
        # would simply reload on the next restart, making "reset" ineffective.
        if self._profile_store is None:
            return
        try:
            data = {
                "learned_sessions": self._learned_sessions,
                "profiles": {k: dict(v) for k, v in self._profiles.items()},
                "profile_total_count": dict(self._profile_total_count),
                "profile_context_count": {
                    pid: dict(counts)
                    for pid, counts in self._profile_context_count.items()
                },
            }
            await self._profile_store.async_save(data)
        except Exception as e:
            _LOGGER.warning("Failed to save intelligent leak profiles: %s", e)

    async def async_reset_learning(self) -> None:
        """Clear learned profiles and immediately rebuild from engine session history.

        Exposed for the `water_monitor.reset_intelligent_leak_learning` service so
        stale/degraded profiles (e.g. lacking spread data from before a model change)
        can be rebuilt without waiting weeks for organic relearning.
        """
        self._profiles = {}
        self._profile_total_count = {}
        self._profile_context_count = {}
        self._learned_sessions = 0
        self._last_finalized_sig = None
        self._bootstrap_profiles_from_engine()
        await self._save_profiles()
        self.async_write_ha_state()

    def _get_engine(self) -> Optional[WaterMonitorEngine]:
        try:
            data = self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id)
            return data.get("engine") if isinstance(data, dict) else None
        except Exception:
            return None

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

    def _current_occ_class(self, now: datetime, eng: Optional[WaterMonitorEngine]) -> str:
        """Resolve occupancy class from engine/occupancy entity only.

        Night classification was removed: it under-populated the 'home' profile
        bucket and caused spurious context mismatches for early-morning use.
        All non-away/vacation states collapse to 'home'.
        """
        occ_class = "home"
        try:
            if eng:
                stats = eng.get_context_stats_for_now()
                raw = str(stats.get("occ_class") or "home")
                if raw in ("away", "vacation"):
                    occ_class = raw
        except Exception:
            pass
        return occ_class

    def _context_key(self, occ_class: str) -> str:
        return occ_class if occ_class in ("home", "night", "away", "vacation") else "home"

    def _feature_from_values(
        self,
        volume_gal: float,
        duration_s: int,
        avg_flow_gpm: float,
        hot_pct: float,
    ) -> dict[str, float]:
        return {
            "volume": max(0.0, float(volume_gal)),
            "duration": max(0.0, float(duration_s)),
            "avg_flow": max(0.0, float(avg_flow_gpm)),
            "hot_pct": max(0.0, min(100.0, float(hot_pct))),
        }

    def _profile_scale(self, profile: dict, key: str, mean_value: float, floor: float) -> float:
        """Return a normalization scale for distance/ceiling calcs.

        Uses the profile's learned spread (half the p10-p90 range) once enough raw
        samples exist; otherwise falls back to a fraction of the mean (today's
        simpler heuristic). This keeps naturally high-variance recurring events
        (irrigation cycles, showers) from looking artificially "novel" just because
        a given run is longer/bigger than average.
        """
        samples = profile.get(f"{key}_samples") or []
        if len(samples) >= 5:
            p90 = percentile_of(samples, 90)
            p10 = percentile_of(samples, 10)
            spread = (p90 - p10) / 2.0
            return max(spread, floor)
        return max(mean_value, floor)

    def _profile_percentile(self, profile: dict, key: str, pct: float, fallback: float) -> float:
        """Return a learned percentile ceiling for a profile metric.

        Falls back to the supplied value when the profile doesn't yet have enough
        raw samples (< 5) to compute a meaningful percentile.
        """
        samples = profile.get(f"{key}_samples") or []
        if len(samples) >= 5:
            return percentile_of(samples, pct)
        return fallback

    def _profile_time_window(self, profile: dict) -> Optional[tuple[float, float]]:
        """Return a learned (start, end) minute-of-day window for this profile.

        Purely data-driven from observed session start times (p10-p90, padded by
        30 minutes) — no hardcoded clock times. Returns None when fewer than 5
        start-time samples have been recorded yet, in which case time-of-day is
        treated as a neutral factor (no bonus, no penalty).
        """
        samples = profile.get("start_minute_samples") or []
        if len(samples) < 5:
            return None
        lo = max(0.0, percentile_of(samples, 10) - 30.0)
        hi = min(1439.0, percentile_of(samples, 90) + 30.0)
        return (lo, hi)

    def _time_of_day_minutes(self, dt_local: datetime) -> int:
        return dt_local.hour * 60 + dt_local.minute

    def _profile_distance(self, feature: dict[str, float], profile: dict) -> float:
        """Weighted normalized distance to a profile centroid.

        Duration/volume are normalized by the profile's learned spread when enough
        samples exist (see `_profile_scale`), instead of always using a bare mean.
        """
        p_dur = max(60.0, float(profile.get("duration", 60.0)))
        p_flow = max(0.2, float(profile.get("avg_flow", 0.2)))
        p_vol = max(0.5, float(profile.get("volume", 0.5)))

        dur_scale = self._profile_scale(profile, "duration", p_dur, 60.0)
        vol_scale = self._profile_scale(profile, "volume", p_vol, 0.5)

        d_dur = (float(feature["duration"]) - p_dur) / dur_scale
        d_flow = (float(feature["avg_flow"]) - p_flow) / p_flow
        d_vol = (float(feature["volume"]) - p_vol) / vol_scale
        d_hot = (float(feature["hot_pct"]) - float(profile.get("hot_pct", 0.0))) / 100.0

        return sqrt((1.2 * d_dur * d_dur) + (1.0 * d_flow * d_flow) + (1.0 * d_vol * d_vol) + (0.5 * d_hot * d_hot))

    def _best_profile(self, feature: dict[str, float]) -> tuple[Optional[str], Optional[dict], float]:
        best_id = None
        best_profile = None
        best_dist = 999.0
        for pid, profile in self._profiles.items():
            dist = self._profile_distance(feature, profile)
            if dist < best_dist:
                best_id = pid
                best_profile = profile
                best_dist = dist
        return best_id, best_profile, best_dist

    def _next_profile_id(self) -> str:
        idx = len(self._profiles) + 1
        return f"t{idx:02d}"

    def _update_profile(self, profile_id: str, feature: dict[str, float], start_minute: Optional[int] = None) -> None:
        profile = self._profiles.get(profile_id)
        if profile is None:
            profile = {
                "volume": float(feature["volume"]),
                "duration": float(feature["duration"]),
                "avg_flow": float(feature["avg_flow"]),
                "hot_pct": float(feature["hot_pct"]),
                "count": 0,
                "duration_samples": [],
                "volume_samples": [],
                "start_minute_samples": [],
            }
            self._profiles[profile_id] = profile

        count = int(profile.get("count", 0)) + 1
        profile["count"] = count

        alpha = 1.0 / float(count)
        for key in ("volume", "duration", "avg_flow", "hot_pct"):
            prev = float(profile.get(key, 0.0))
            cur = float(feature.get(key, 0.0))
            profile[key] = prev + (cur - prev) * alpha

        # Maintain bounded raw-sample history for percentile/spread-based scoring.
        # Cap at 60 samples per metric (roughly matches the long-horizon learning
        # window called out in docs/INTELLIGENT_LEAK_DESIGN.md).
        dur_samples = profile.setdefault("duration_samples", [])
        dur_samples.append(float(feature["duration"]))
        if len(dur_samples) > 60:
            del dur_samples[: len(dur_samples) - 60]

        vol_samples = profile.setdefault("volume_samples", [])
        vol_samples.append(float(feature["volume"]))
        if len(vol_samples) > 60:
            del vol_samples[: len(vol_samples) - 60]

        if start_minute is not None:
            start_samples = profile.setdefault("start_minute_samples", [])
            start_samples.append(int(start_minute))
            if len(start_samples) > 60:
                del start_samples[: len(start_samples) - 60]

    def _record_profile_context(self, profile_id: str, ctx_key: str) -> None:
        self._profile_total_count[profile_id] = int(self._profile_total_count.get(profile_id, 0)) + 1
        counts = self._profile_context_count.setdefault(profile_id, {})
        counts[ctx_key] = int(counts.get(ctx_key, 0)) + 1

    def _learn_session(self, feature: dict[str, float], ctx_key: str, start_minute: Optional[int] = None) -> tuple[str, float]:
        """Assign a session to a learned profile and update centroids."""
        self._learned_sessions += 1

        best_id, _best_profile, best_dist = self._best_profile(feature)
        merge_threshold = 0.85
        max_profiles = 8

        if best_id is None:
            best_id = self._next_profile_id()
            self._update_profile(best_id, feature, start_minute)
            self._record_profile_context(best_id, ctx_key)
            return best_id, 0.0

        if best_dist > merge_threshold and len(self._profiles) < max_profiles:
            new_id = self._next_profile_id()
            self._update_profile(new_id, feature, start_minute)
            self._record_profile_context(new_id, ctx_key)
            return new_id, best_dist

        self._update_profile(best_id, feature, start_minute)
        self._record_profile_context(best_id, ctx_key)
        return best_id, best_dist

    def _bootstrap_profiles_from_engine(self) -> None:
        """Initialize in-memory profiles from persisted engine session history."""
        eng = self._get_engine()
        if not eng:
            return
        try:
            sessions = eng.get_recent_sessions(300)
        except Exception:
            return
        for rec in sessions:
            try:
                dur = int(rec.get("duration_s", 0) or 0)
                vol = float(rec.get("volume", 0.0) or 0.0)
                avg = float(rec.get("avg_flow", 0.0) or 0.0)
                hot = float(rec.get("hot_pct", 0.0) or 0.0)
            except Exception:
                continue
            if dur <= 0 or vol <= 0:
                continue
            start_minute = None
            try:
                ended_dt = datetime.fromisoformat(rec.get("ended_at"))
                start_dt = (ended_dt - timedelta(seconds=dur)).astimezone()
                start_minute = start_dt.hour * 60 + start_dt.minute
            except Exception:
                start_minute = None
            feature = self._feature_from_values(vol, dur, avg, hot)
            ctx_key = self._context_key(str(rec.get("occ_class") or "home"))
            self._learn_session(feature, ctx_key, start_minute)

    def _is_learning_period(self) -> tuple[bool, str]:
        """
        Check if detector is still in learning period.
        Returns (is_learning, reason).
        """
        ex = {**self._entry.data, **self._entry.options}

        min_learning_days = int(ex.get(CONF_INTEL_MINIMUM_LEARNING_DAYS, 14))
        min_samples = max(20, min_learning_days * 3)
        if self._learned_sessions < min_samples:
            return True, f"Learning period: {self._learned_sessions} samples < {min_samples} minimum"
        return False, "Learning period complete"

    def _suppress_during_learning_enabled(self) -> bool:
        """Return whether learning-mode alert suppression is enabled."""
        ex = {**self._entry.data, **self._entry.options}
        return bool(ex.get(CONF_INTEL_SUPPRESS_NOTIFICATIONS_DURING_LEARNING, True))

    def _profile_confidence(self, profile_id: Optional[str], ctx_key: str) -> float:
        """Estimate confidence that a profile is normal in the current context."""
        if not profile_id:
            return 0.0

        total = int(self._profile_total_count.get(profile_id, 0))
        ctx_count = int(self._profile_context_count.get(profile_id, {}).get(ctx_key, 0))
        if total <= 0:
            return 0.0
        if ctx_count < 5:
            return min(0.5, ctx_count * 0.15)
        return min(1.0, (ctx_count / float(total)) * 0.9 + 0.1)

    def _check_corroboration(self) -> tuple[bool, Optional[str]]:
        """Check whether another leak detector currently reports a confirmed leak.

        This is a fast corroboration signal, not a hard dependency: if either
        detector is disabled/absent, this simply returns (False, None) and
        Intelligent Leak continues to rely entirely on its own scoring.
        """
        for entity_id, source in (
            (self._low_flow_entity_id, "low_flow"),
            (self._tank_refill_entity_id, "tank_refill"),
        ):
            if not entity_id:
                continue
            st = self.hass.states.get(entity_id)
            if st is not None and st.state == "on":
                return True, source
        return False, None

    def _thresholds_from_sensitivity(self, sensitivity: float) -> tuple[float, float, float, int, int]:
        """Return (potential_th, confirmed_th, clear_th, potential_hold_s, confirmed_hold_s)."""
        s = max(0.0, min(100.0, sensitivity))
        # Raised potential threshold floor to reduce hair-trigger Potential alerts.
        potential_th = max(0.90, 1.40 - (0.005 * s))
        confirmed_th = potential_th + 0.55
        # clear_th must be meaningfully below potential to prevent sticking.
        clear_th = max(0.50, potential_th * 0.60)
        # Potential hold must exceed typical short-session duration (handwash ~30s, toilet ~45s).
        # Floor at 120s so brief bursts of high novelty cannot complete a Potential transition.
        potential_hold_s = max(120, int(240 - (1.5 * s)))
        confirmed_hold_s = max(300, int(600 - (3.0 * s)))
        return potential_th, confirmed_th, clear_th, potential_hold_s, confirmed_hold_s

    def _is_expected_in_context(self, profile_id: Optional[str], ctx_key: str) -> bool:
        if not profile_id:
            return False
        total = int(self._profile_total_count.get(profile_id, 0))
        if total < 2:
            return False
        ctx = int(self._profile_context_count.get(profile_id, {}).get(ctx_key, 0))
        return (ctx / float(total)) >= 0.12

    def _learn_from_last_session(self, state: dict, now: datetime, ctx_key: str) -> tuple[Optional[str], Optional[float]]:
        """Consume finalized session payload (if new) and update profile model."""
        try:
            vol = float(state.get("last_session_volume", 0.0) or 0.0)
            dur = int(state.get("last_session_duration", 0) or 0)
            avg = float(state.get("last_session_average_flow", 0.0) or 0.0)
            hot = float(state.get("last_session_hot_water_pct", 0.0) or 0.0)
            synth = float(state.get("last_session_synthetic_volume", 0.0) or 0.0)
        except Exception:
            return None, None

        vol_eff = max(0.0, vol - max(0.0, synth))
        if dur <= 0 or vol_eff <= 0:
            return None, None

        # Debounce repeated tracker payloads for the same finalized session.
        sig = (round(vol_eff, 4), int(dur), round(avg, 4))
        if sig == self._last_finalized_sig:
            return None, None
        self._last_finalized_sig = sig

        # Adjust avg flow to remove synthetic contribution.
        if dur > 0 and synth > 0:
            avg = max(0.0, avg - (synth / (dur / 60.0)))

        start_minute = None
        try:
            start_dt = (now - timedelta(seconds=dur)).astimezone()
            start_minute = start_dt.hour * 60 + start_dt.minute
        except Exception:
            start_minute = None

        feature = self._feature_from_values(vol_eff, dur, avg, hot)
        if self._session_leak_corroborated:
            # This session was confirmed as a leak by another detector — don't let
            # it get folded into "normal" profile centroids, or a recurring leak
            # would progressively look more and more like expected usage.
            return None, None
        profile_id, distance = self._learn_session(feature, ctx_key, start_minute)
        return profile_id, distance

    def _live_feature(self, state: dict, elapsed_s: int) -> dict[str, float]:
        try:
            volume = float(state.get("current_session_volume", 0.0) or 0.0)
        except Exception:
            volume = 0.0
        try:
            avg_flow = float(state.get("current_session_average_flow", 0.0) or 0.0)
        except Exception:
            avg_flow = 0.0
        try:
            hot_pct = float(state.get("current_session_hot_water_pct", 0.0) or 0.0)
        except Exception:
            hot_pct = 0.0

        # Fallback: infer current volume from avg flow and elapsed if tracker volume is absent.
        if volume <= 0.0 and elapsed_s > 0 and avg_flow > 0.0:
            volume = avg_flow * (float(elapsed_s) / 60.0)

        return self._feature_from_values(volume, elapsed_s, avg_flow, hot_pct)

    @callback
    def _on_tracker_update(self, state: dict) -> None:
        try:
            valve_entity, valve_off, auto, effective = self._get_valve_context(CONF_INTEL_AUTO_SHUTOFF)
            now = datetime.now(timezone.utc)
            prev_eval_ts = self._last_eval_ts
            self._last_eval_ts = now

            # Cross-detector corroboration: if Low-Flow or Tank-Refill independently
            # confirm a leak during this session, latch it (sticky for the rest of
            # the session) as a strong signal — our own novelty/profile scoring can
            # be slow (or unable) to flag a recurring-but-genuine leak on its own.
            if not self._session_leak_corroborated:
                corroborated_now, corroboration_source = self._check_corroboration()
                if corroborated_now:
                    self._session_leak_corroborated = True
                    self._corroboration_source = corroboration_source

            # Pull live session metrics
            active = bool(state.get("current_session_active", False))
            elapsed = int(state.get("current_session_duration", 0) or 0)
            avg_flow = float(state.get("current_session_average_flow", 0.0) or 0.0)
            hot_pct = float(state.get("current_session_hot_water_pct", 0.0) or 0.0)
            flow_now = float(state.get("flow_sensor_value", 0.0) or 0.0)

            # Accumulate continuous zero-flow duration (debounce for flow-based
            # instant-clear checks below). Clamp dt to avoid huge jumps across HA
            # restarts or long dispatcher gaps.
            dt = (now - prev_eval_ts).total_seconds() if prev_eval_ts else 0.0
            dt = max(0.0, min(dt, 300.0))
            if flow_now > 0.0:
                self._idle_zero_s = 0.0
            else:
                self._idle_zero_s += dt

            # Maintain independent wall-clock elapsed while flow > 0.
            if flow_now > 0.0:
                if self._flow_active_start is None:
                    self._flow_active_start = now
            else:
                self._flow_active_start = None
            flow_elapsed = int((now - self._flow_active_start).total_seconds()) if self._flow_active_start else 0

            # Choose elapsed for risk: prefer session elapsed, else fall back to wall-clock under flow.
            eff_elapsed = elapsed if (active and elapsed > 0) else flow_elapsed
            eng = self._get_engine()
            occ_class = self._current_occ_class(now, eng)
            ctx_key = self._context_key(occ_class)

            # Keep learning model updated from newly finalized sessions.
            learned_profile_id, learned_distance = self._learn_from_last_session(state, now, ctx_key)
            # Persist updated profiles after each new real session is learned.
            if learned_profile_id is not None:
                self.hass.async_create_task(self._save_profiles())

            sensitivity = self._get_sensitivity()

            potential_th, confirmed_th, clear_th, potential_hold_s, confirmed_hold_s = self._thresholds_from_sensitivity(sensitivity)

            live_feature = self._live_feature(state, eff_elapsed)
            best_id, best_profile, novelty = self._best_profile(live_feature)
            expected_in_context = self._is_expected_in_context(best_id, ctx_key)
            profile_confidence = self._profile_confidence(best_id, ctx_key)

            # Risk is mostly novelty + persistence. Context acts as a modifier.
            reasons = []
            risk = 0.0
            novelty_component = 0.0
            duration_component = 0.0
            volume_component = 0.0
            context_component = 0.0
            away_component = 0.0
            low_flow_component = 0.0
            corroboration_component = 0.0
            time_window_discount = 0.0
            time_window_match: Optional[bool] = None

            novelty_norm = min(1.5, novelty) / 3.0
            novelty_component = novelty_norm * 0.8
            if best_id is not None and profile_confidence >= 0.60:
                # High-confidence profile match discounts novelty specifically (this
                # pattern closely resembles known routine usage) — but must NOT
                # discount duration/volume/low-flow overrun signals, which indicate
                # genuine persistence beyond what any known routine would produce.
                novelty_component = max(0.0, novelty_component - min(0.20, profile_confidence * 0.20))
                reasons.append("high_confidence_profile")
            risk += novelty_component
            reasons.append("novelty")

            if best_profile is not None:
                # Learned percentile ceilings (p95 of this profile's own history) instead
                # of a fixed mean-based tolerance — recurring events with natural
                # variability (irrigation cycle length, shower duration/volume)
                # shouldn't be flagged just for running longer/bigger than average.
                # Falls back to the old mean-based heuristic until a profile has
                # accumulated enough samples (see `_profile_percentile`).
                fallback_duration_ceiling = max(120.0, float(best_profile.get("duration", 120.0)) * 1.15)
                duration_ceiling = self._profile_percentile(best_profile, "duration", 95, fallback_duration_ceiling)
                if eff_elapsed > duration_ceiling and eff_elapsed >= 600:
                    duration_ratio = float(eff_elapsed) / max(1.0, duration_ceiling)
                    duration_component = min(0.5, max(0.0, (duration_ratio - 1.0) * 0.5))
                    risk += duration_component
                    reasons.append("persistent_runtime")

                fallback_volume_ceiling = max(0.5, float(best_profile.get("volume", 0.5)) * 1.3)
                volume_ceiling = self._profile_percentile(best_profile, "volume", 95, fallback_volume_ceiling)
                live_volume = float(live_feature.get("volume", 0.0))
                if live_volume > volume_ceiling:
                    volume_ratio = live_volume / max(0.1, volume_ceiling)
                    volume_component = min(0.3, max(0.0, (volume_ratio - 1.0) * 0.4))
                    risk += volume_component
                    reasons.append("volume_overrun")

                # Learned time-of-day window (p10-p90 of observed start times, ±30min
                # padding). Purely data-driven — no hardcoded clock times. If the
                # current session's start falls inside this profile's normal window,
                # discount the overrun components: a scheduled irrigation cycle that
                # always runs in the same early-morning window naturally gets a tight
                # window, while anytime showers naturally get a wide/permissive one.
                time_window = self._profile_time_window(best_profile)
                if time_window is not None:
                    lo, hi = time_window
                    session_start = now.astimezone() - timedelta(seconds=eff_elapsed)
                    start_minute = self._time_of_day_minutes(session_start)
                    time_window_match = lo <= start_minute <= hi
                    if time_window_match and (duration_component > 0.0 or volume_component > 0.0):
                        time_window_discount = min(duration_component + volume_component, 0.35)
                        risk -= time_window_discount
                        reasons.append("expected_time_window")

                # Use session avg_flow (smoothed over the session) instead of instantaneous
                # flow_now. Instantaneous flow is too noisy during ramp-up/ramp-down and
                # caused large flow_delta spikes on every short session.
                profile_flow = max(0.2, float(best_profile.get("avg_flow", 0.2)))
                compare_flow = avg_flow if avg_flow > 0.0 else flow_now
                flow_delta = abs(compare_flow - profile_flow) / profile_flow
                risk += min(0.40, flow_delta * 0.20)
            else:
                # Unknown pattern: only become risky once it persists for a bit.
                if eff_elapsed >= 180:
                    risk += 0.50
                    reasons.append("unknown_profile")

            if best_id is not None:
                total_profile_samples = int(self._profile_total_count.get(best_id, 0))
                ctx_occurrence = int(self._profile_context_count.get(best_id, {}).get(ctx_key, 0))
                if total_profile_samples >= 10 and (ctx_occurrence / max(1, total_profile_samples)) < 0.08:
                    context_component = 0.15
                    risk += context_component
                    reasons.append("context_mismatch")

            if 0.0 < flow_now <= 0.30 and eff_elapsed >= 10 * 60:
                # Scale with time sustained past the 10-minute floor instead of a
                # flat bump, so a genuinely long-running drip accumulates
                # meaningfully more risk on its own, independent of corroboration.
                over_s = eff_elapsed - (10 * 60)
                low_flow_component = min(0.8, 0.20 + (over_s / 600.0) * 0.20)
                risk += low_flow_component
                reasons.append("low_flow_persistent")

            # Away/vacation should be more conservative for unexpected usage.
            if ctx_key in ("away", "vacation") and not expected_in_context and profile_confidence < 0.60:
                away_component = 0.10
                risk += away_component
                reasons.append("away_modifier")

            if self._session_leak_corroborated:
                # Another detector already confirmed this session is a leak — add a
                # strong, dominant contribution so Intelligent Leak reaches
                # Confirmed quickly rather than waiting on its own scoring/timers.
                corroboration_component = 1.8
                risk += corroboration_component
                reasons.append("corroborated_by_other_detector")

            risk = min(risk, 2.5)

            # Stage transition candidates (hysteresis driven).
            # Require minimum elapsed before Potential candidate can start — prevents
            # short-burst sessions (handwash, toilet) from ever accumulating hold time.
            min_elapsed_for_potential = 120
            if flow_now > 0.0 and risk >= potential_th and eff_elapsed >= min_elapsed_for_potential:
                if self._potential_candidate_since is None:
                    self._potential_candidate_since = now
            else:
                self._potential_candidate_since = None

            if flow_now > 0.0 and risk >= confirmed_th:
                if self._confirmed_candidate_since is None:
                    self._confirmed_candidate_since = now
            else:
                self._confirmed_candidate_since = None

            prev_stage = self._leak_stage
            stage = prev_stage

            # Learning state and suppression policy.
            learning_period, learning_reason = self._is_learning_period()
            suppress_during_learning = self._suppress_during_learning_enabled()

            # A flow-based "it just stopped" clear is only honored once zero flow
            # has been sustained for _CLEAR_IDLE_S seconds. A single momentary zero
            # reading (pulsing/intermittent leak, e.g. a cycling toilet flapper)
            # must not clear an already-alarmed stage, or it will instantly
            # re-trigger on the next tick and flap on/off.
            sustained_idle = self._idle_zero_s >= self._CLEAR_IDLE_S

            if not active and flow_now <= 0.0 and (prev_stage == "normal" or sustained_idle):
                if prev_stage == "confirmed" and valve_off:
                    stage = "confirmed"
                else:
                    stage = "normal"
                self._potential_candidate_since = None
                self._confirmed_candidate_since = None
                self._session_leak_corroborated = False
                self._corroboration_source = None
            elif learning_period and suppress_during_learning:
                # Fresh installs and learning periods must be non-alerting when suppression is enabled.
                stage = "normal"
                self._potential_candidate_since = None
                self._confirmed_candidate_since = None
            else:
                potential_ready = bool(
                    self._potential_candidate_since and (now - self._potential_candidate_since).total_seconds() >= potential_hold_s
                )
                confirmed_ready = bool(
                    self._confirmed_candidate_since and (now - self._confirmed_candidate_since).total_seconds() >= confirmed_hold_s
                )
                if self._session_leak_corroborated:
                    # Another detector already enforced its own persistence
                    # requirement (seed/min-duration) — skip our own hold timers.
                    potential_ready = True
                    confirmed_ready = True

                if prev_stage == "confirmed":
                    if valve_off:
                        stage = "confirmed"
                    elif flow_now <= 0.0:
                        # Zero flow: this is the "leak may have stopped" path.
                        # Gate ALL clearing (including a risk drop, which often
                        # accompanies session end) behind the sustained-idle
                        # debounce so a single zero-flow tick can't clear the
                        # alarm and have it instantly re-confirm on the next
                        # tick (flapping).
                        stage = "normal" if sustained_idle else "confirmed"
                    elif risk < clear_th:
                        stage = "normal"
                    else:
                        stage = "confirmed"
                elif confirmed_ready:
                    stage = "confirmed"
                elif potential_ready:
                    stage = "potential"
                elif risk < clear_th:
                    stage = "normal"
                else:
                    stage = prev_stage

            # Capture synthetic flow when entering confirmed stage.
            if prev_stage != "confirmed" and stage == "confirmed":
                self._synthetic_flow_at_trigger = float(state.get("synthetic_flow_gpm", 0.0) or 0.0)

            if learning_period and stage == "normal":
                trigger_reason = f"Learning period active: {learning_reason}"
            elif stage == "confirmed":
                trigger_reason = "Confirmed leak: persistent novel usage pattern"
            elif stage == "potential":
                trigger_reason = "Potential leak: atypical usage pattern detected"
            else:
                trigger_reason = "No leak detected"

            self._leak_stage = stage
            self._attr_is_on = stage in ("potential", "confirmed")

            trigger_threshold = confirmed_th if stage == "confirmed" else potential_th
            self._attr_extra_state_attributes = {
                # Universal attributes
                "leak_type": "intelligent",
                "leak_stage": stage,
                "trigger_reason": trigger_reason,
                "current_flow": round(flow_now, 2),
                "elapsed_s": eff_elapsed,
                "trigger_value": round(risk, 3),
                "trigger_threshold": round(trigger_threshold, 3),
                "trigger_unit": "risk",
                "hot_pct": round(hot_pct, 1),
                "idle_zero_s": round(self._idle_zero_s, 1),
                "clear_idle_s": self._CLEAR_IDLE_S,

                # Intelligent-specific attributes
                "current_usage": round(avg_flow, 2),
                "usage_context": ctx_key,
                "sensitivity_setting": sensitivity,
                "risk": round(risk, 3),
                "reasons": reasons,
                "profile_id": best_id,
                "profile_distance": round(novelty, 3),
                "expected_in_context": expected_in_context,
                "profile_confidence": round(profile_confidence, 3),
                "known_profiles": len(self._profiles),
                "learned_sessions": self._learned_sessions,
                "learned_profile_id": learned_profile_id,
                "learned_profile_distance": round(learned_distance, 3) if learned_distance is not None else None,
                "potential_hold_s": potential_hold_s,
                "confirmed_hold_s": confirmed_hold_s,
                "novelty_component": round(novelty_component, 3),
                "duration_component": round(duration_component, 3),
                "volume_component": round(volume_component, 3),
                "context_component": round(context_component, 3),
                "away_component": round(away_component, 3),
                "low_flow_component": round(low_flow_component, 3),
                "time_window_match": time_window_match,
                "time_window_discount": round(time_window_discount, 3),
                "corroboration_component": round(corroboration_component, 3),
                "corroborated_leak": self._session_leak_corroborated,
                "corroboration_source": self._corroboration_source,
                "potential_candidate_s": int((now - self._potential_candidate_since).total_seconds()) if self._potential_candidate_since else 0,
                "confirmed_candidate_s": int((now - self._confirmed_candidate_since).total_seconds()) if self._confirmed_candidate_since else 0,

                # Auto-shutoff attributes
                "auto_shutoff_on_trigger": auto,
                "auto_shutoff_effective": effective,
                "auto_shutoff_valve_entity": valve_entity,
                "valve_off": valve_off,
                "synthetic_flow_at_trigger": self._synthetic_flow_at_trigger,

                # Learning period status
                "learning_period_active": learning_period,
                "learning_status": learning_reason,
                "learning_suppression_enabled": suppress_during_learning,
            }

            # Auto-shutoff only when entering confirmed stage.
            if (
                prev_stage != "confirmed"
                and stage == "confirmed"
                and effective
                and valve_entity
                and not (learning_period and suppress_during_learning)
            ):
                self._async_call_valve_off(valve_entity)

            prev_on = prev_stage in ("potential", "confirmed")
            # Write on state change or stage transition (rare) for observability.
            if prev_on != self._attr_is_on or prev_stage != stage:
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
        valve_entity_id: Optional[str] = None,
    ) -> None:
        self._entry = entry
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_upstream_health"
        self._attr_is_on = True
        self._attr_available = True
        self._attr_extra_state_attributes = {}

        self._flow_entity_id = flow_entity_id
        # Normalize optional IDs: treat empty strings as None
        self._volume_entity_id = volume_entity_id or None
        self._hot_water_entity_id = hot_water_entity_id or None
        self._valve_entity_id = valve_entity_id or None

        self._unsub_state = None
        # Track when each entity became unavailable for debouncing
        self._unavailable_since: dict[str, datetime] = {}
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
            for e in [
                self._flow_entity_id,
                self._volume_entity_id,
                self._hot_water_entity_id,
                self._valve_entity_id,
            ]
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

    def _is_valve_ok(self) -> Optional[bool]:
        if not self._valve_entity_id:
            return None
        st = self.hass.states.get(self._valve_entity_id)
        if not st or st.state in (None, "unknown", "unavailable"):
            return False
        # Any valid on/off-ish state is considered OK; we don't judge open/closed here
        return str(st.state).lower() in ("on", "off", "open", "closed", "true", "false", "0", "1")

    async def _evaluate(self, now: datetime) -> None:
        # Update our debounce tracking: note when entities enter an unhealthy state
        # and clear the timestamp once they recover. This allows the subsequent
        # cutoff logic to only mark them as actually unavailable/unknown after
        # 60 seconds have passed in that state.
        for ent_id in [
            self._flow_entity_id,
            self._volume_entity_id,
            self._hot_water_entity_id,
            self._valve_entity_id,
        ]:
            if not ent_id:
                continue
            st = self.hass.states.get(ent_id)
            if not st or st.state in (None, "unknown", "unavailable"):
                # start timer if not already running
                self._unavailable_since.setdefault(ent_id, now)
            else:
                # recovered, drop any prior timestamp
                self._unavailable_since.pop(ent_id, None)

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
                # record time when this upstream was last seen good
                self._last_ok[ent_id] = now
            if ok is False:
                # add to list, but we'll debounce later using last_ok time
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
        valve_ok = self._is_valve_ok()
        if self._valve_entity_id is not None:
            upd(self._valve_entity_id, valve_ok)

        # Debounce transient outages: only report as unavailable if the entity has been
        # in that state for 60+ seconds. Use our tracked unavailable_since time.
        debounced_unavailable = []
        for ent_id in unavailable:
            since = self._unavailable_since.get(ent_id)
            if since and (now - since).total_seconds() > 60:
                debounced_unavailable.append(ent_id)
        unavailable = debounced_unavailable

        # Same for unknown
        debounced_unknown = []
        for ent_id in unknown:
            since = self._unavailable_since.get(ent_id)
            if since and (now - since).total_seconds() > 60:
                debounced_unknown.append(ent_id)
        unknown = debounced_unknown

        # Overall status should reflect the *debounced* results rather than
        # the instantaneous raw readings.  A sensor is considered healthy only
        # if it has not been unavailable/unknown for more than the debounce
        # period.  Valve availability is irrelevant to upstream health.
        prev_on = self._attr_is_on
        prev_attrs = dict(self._attr_extra_state_attributes) if self._attr_extra_state_attributes else {}
        status = True

        # helper to mark unhealthy if entity is in either debounced list
        def mark_if(entity_id: Optional[str]) -> None:
            nonlocal status
            if entity_id and (entity_id in unavailable or entity_id in unknown):
                status = False

        mark_if(self._flow_entity_id)
        mark_if(self._volume_entity_id)
        if self._hot_water_entity_id:
            # also require that the hot_water check itself returned True (ok)
            if hot_ok is not True:
                status = False
            mark_if(self._hot_water_entity_id)

        self._attr_is_on = bool(status)
        self._attr_extra_state_attributes = {
            "unavailable_entities": unavailable,
            "unknown_entities": unknown,
            "name_to_entity": name_to_entity,
            **per_name_last_ok,
        }
        # Write if the on/off state changed OR the diagnostic attributes
        # changed (e.g. per-entity last-ok timestamps updating while overall
        # health stays the same).
        if prev_on != self._attr_is_on or self._attr_extra_state_attributes != prev_attrs:
            self.async_write_ha_state()


class LowFlowLeakBinarySensor(LeakDetectorBase):
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
        super().__init__(entry, name)
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
        # Track when each entity became unavailable for debouncing
        self._unavailable_since: dict[str, datetime] = {}
        # Track detectors_flow provided by the tracker (includes synthetic when enabled)
        self._tracker_unsub = None
        self._last_detectors_flow = None
        self._current_synthetic_flow = 0.0

        # Runtime counters
        self._seeded = False
        self._seed_progress = 0.0
        self._count_progress = 0.0
        self._idle_zero_s = 0.0
        self._high_flow_s = 0.0
        self._last_update = None
        self._cooldown_until = None
        # Track synthetic flow at trigger time for notifications
        self._synthetic_flow_at_trigger = 0.0

        # Throttle database writes to reduce recorder load while maintaining UI responsiveness
        self._last_write_time = None

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
            # Also track current synthetic flow for leak detection
            self._current_synthetic_flow = float(state.get("synthetic_flow_gpm", 0.0) or 0.0)
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

        # Clear conditions (suppressed when valve is off)
        data = self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id)
        valve_off = bool(data.get("valve_off", False)) if isinstance(data, dict) else False
        cleared = False
        if self._attr_is_on:
            if not valve_off:
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
        prev_on = self._attr_is_on
        prev_attrs = dict(self._attr_extra_state_attributes) if self._attr_extra_state_attributes else {}
        if not self._attr_is_on and can_trigger and self._seeded and self._count_progress >= self._min_s:
            self._attr_is_on = True
            # Capture synthetic flow at trigger time for notifications
            self._synthetic_flow_at_trigger = getattr(self, '_current_synthetic_flow', 0.0)
        # If turning on and auto-shutoff is enabled, request valve off
        valve_ent, valve_off, auto, effective = self._get_valve_context(CONF_LOW_FLOW_AUTO_SHUTOFF)
        if not prev_on and self._attr_is_on and effective and valve_ent:
            self._async_call_valve_off(valve_ent)

        # Phase
        if self._attr_is_on:
            phase = "alarmed"
        elif not self._seeded:
            phase = "seeding" if counting_active else "idle"
        else:
            phase = "counting" if counting_active else "idle"

        # Build human-readable trigger reason
        if self._attr_is_on:
            trigger_reason = f"Sustained low flow detected ({self._counting_mode} mode)"
            if flow > 0:
                trigger_reason += f" at {flow:.2f} GPM for {self._count_progress:.0f}s"
        else:
            if phase == "seeding":
                trigger_reason = f"Building confidence ({self._seed_progress:.0f}s of {self._seed_s}s required)"
            elif phase == "counting":
                trigger_reason = f"Monitoring flow ({self._count_progress:.0f}s of {self._min_s}s required)"
            else:
                trigger_reason = "No sustained flow detected"

        # Get hot water percentage from tracker
        try:
            tracker_data = self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id, {})
            hot_pct = float(tracker_data.get("current_session_hot_water_pct", 0.0) or 0.0)
        except Exception:
            hot_pct = 0.0

        self._attr_extra_state_attributes = {
            # Universal attributes
            "leak_type": "low_flow",
            "trigger_reason": trigger_reason,
            "current_flow": round(flow, 2),
            "elapsed_s": round(self._count_progress, 1),
            "trigger_value": round(self._count_progress, 1),
            "trigger_threshold": float(self._min_s),
            "trigger_unit": "seconds",
            "hot_pct": round(hot_pct, 1),
            
            # Low-flow specific attributes (kept for configuration understanding)
            "mode": self._counting_mode,
            "phase": phase,
            "max_low_flow": self._max_low_flow,
            "seed_required_s": self._seed_s,
            "seed_progress_s": round(self._seed_progress, 1),
            "min_duration_s": self._min_s,
            "idle_zero_s": round(self._idle_zero_s, 1),
            "high_flow_s": round(self._high_flow_s, 1),
            "clear_idle_s": self._clear_idle_s,
            "clear_on_high_s": self._clear_on_high_s,
            "cooldown_s": self._cooldown_s,
            "cooldown_until": self._cooldown_until.isoformat() if self._cooldown_until else None,
            "smoothing_s": self._smoothing_s,
            "baseline_margin_pct": self._baseline_margin_pct,
            
            # Auto-shutoff attributes
            "auto_shutoff_on_trigger": auto,
            "auto_shutoff_effective": effective,
            "auto_shutoff_valve_entity": (valve_ent or None),
            "valve_off": valve_off,
            "synthetic_flow_at_trigger": self._synthetic_flow_at_trigger,
        }
        self._last_update = now

        # Write to the recorder when the on/off state changes, or when the
        # diagnostic attributes change (e.g. elapsed_s/phase progressing while
        # still counting toward a trigger) so listeners see live progress
        # instead of only the alarm transitions.
        state_changed = prev_on != self._attr_is_on
        attrs_changed = self._attr_extra_state_attributes != prev_attrs
        if state_changed or attrs_changed:
            self.async_write_ha_state()


class TankRefillLeakBinarySensor(LeakDetectorBase):
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
        max_hot_water_pct: float,
    ) -> None:
        super().__init__(entry, name)
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
        self._max_hot_water_pct = float(max_hot_water_pct)

        # Source entity (resolved by unique_id lookup)
        self._source_entity_id: Optional[str] = None
        self._unsub_state = None
        self._tracker_unsub = None

        # Event memory (ts, volume, duration)
        self._history: Deque[Tuple[datetime, float, int]] = deque()
        self._last_event_ts: Optional[datetime] = None
        self._cooldown_until: Optional[datetime] = None
        self._last_seen_pair: Optional[Tuple[float, int]] = None
        # Track synthetic flow at trigger time for notifications
        self._synthetic_flow_at_trigger = 0.0
        # Track current synthetic flow for consistent capture
        self._current_synthetic_flow = 0.0

        # Periodic timer handle for idle clearing
        self._unsub_timer = None

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
        # Initialize attributes so UI shows expected fields before first event
        try:
            ex = {**self._entry.data, **self._entry.options}
            valve_ent = ex.get(CONF_WATER_SHUTOFF_ENTITY) or ""
            auto = bool(ex.get(CONF_TANK_LEAK_AUTO_SHUTOFF, False))
            data = self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id)
            valve_off = bool(data.get("valve_off", False)) if isinstance(data, dict) else False
            self._attr_extra_state_attributes = {
                "events_in_window": 0,
                "similar_count": 0,
                "min_refill_volume": self._min_volume,
                "max_refill_volume": self._max_volume,
                "tolerance_pct": self._tol_pct,
                "repeat_count": self._repeat,
                "window_s": self._window_s,
                "clear_idle_s": self._clear_idle_s,
                "cooldown_s": self._cooldown_s,
                "last_event": None,
                "min_refill_duration_s": self._min_duration_s,
                "max_refill_duration_s": self._max_duration_s,
                "max_hot_water_pct": self._max_hot_water_pct,
                "contributing_events": [],
                # Auto-shutoff attributes
                "auto_shutoff_on_trigger": auto,
                "auto_shutoff_effective": bool(valve_ent and auto),
                "auto_shutoff_valve_entity": (valve_ent or None),
                "valve_off": valve_off,
            }
        except Exception:
            pass
        self.async_write_ha_state()
        
        # Subscribe to tracker updates for valve state changes
        self._tracker_unsub = async_dispatcher_connect(
            self.hass, tracker_signal(self._entry.entry_id), self._on_tracker_update
        )
        
        # Schedule periodic evaluation so that idle-time clears and attribute
        # refreshes happen even when no new sessions occur.
        self._unsub_timer = async_track_time_interval(
            self.hass, self._async_tick, timedelta(seconds=30)
        )

        await self._resolve_and_subscribe()

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
    def _on_tracker_update(self, state: dict) -> None:
        """Update attributes when valve state or other tracker data changes."""
        try:
            # Update current synthetic flow for consistent capture
            data = self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id)
            if isinstance(data, dict):
                self._current_synthetic_flow = float(data.get("synthetic_flow_gpm", 0.0) or 0.0)
            
            # Get current valve context to update attributes
            valve_ent, valve_off, auto, effective = self._get_valve_context(CONF_TANK_LEAK_AUTO_SHUTOFF)
            
            # Update valve-related attributes
            self._attr_extra_state_attributes.update({
                "auto_shutoff_on_trigger": auto,
                "auto_shutoff_effective": effective,
                "auto_shutoff_valve_entity": (valve_ent or None),
                "valve_off": valve_off,
            })
            
            self.async_write_ha_state()
        except Exception:
            # Defensive: don't throw from callback
            pass

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

    async def _async_tick(self, now: datetime) -> None:
        """Periodic callback to re-evaluate state based on elapsed time."""
        await self._evaluate(now)

    async def _evaluate(self, now: datetime) -> None:
        # Update current synthetic flow for consistent capture
        try:
            data = self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id)
            if isinstance(data, dict):
                self._current_synthetic_flow = float(data.get("synthetic_flow_gpm", 0.0) or 0.0)
        except Exception:
            self._current_synthetic_flow = 0.0
            
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
        try:
            synthetic_vol = float(main.attributes.get("last_session_synthetic_volume", 0.0) or 0.0)
        except (ValueError, TypeError):
            synthetic_vol = 0.0
        
        try:
            hot_water_pct = float(main.attributes.get("last_session_hot_water_pct", 0.0) or 0.0)
        except (ValueError, TypeError):
            hot_water_pct = 0.0

        # Only record when the pair changes
        if self._last_seen_pair != (vol, dur):
            self._last_seen_pair = (vol, dur)
            # Apply duration gates (0 disables)
            duration_ok = True
            if self._min_duration_s > 0 and dur < self._min_duration_s:
                duration_ok = False
            if self._max_duration_s > 0 and dur > self._max_duration_s:
                duration_ok = False

            # Apply hot water percentage gate
            hot_water_ok = hot_water_pct <= self._max_hot_water_pct

            if duration_ok and hot_water_ok and vol >= self._min_volume and (
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
                    # Derive a localized human-friendly time string for notifications
                    try:
                        local_dt = dt_util.as_local(ts)
                        # 12-hour clock, remove leading zero for hours (cross-platform safe)
                        local_time = local_dt.strftime("%I:%M%p").lstrip("0").lower()  # e.g., "3:00pm"
                    except Exception:
                        local_time = None
                    contributing.append({
                        "ts": ts.isoformat(),
                        "local_time": local_time,
                        "volume": v,
                        "duration_s": d,
                    })

        prev_on = self._attr_is_on
        prev_attrs = dict(self._attr_extra_state_attributes) if self._attr_extra_state_attributes else {}
        # Read valve context
        valve_ent, valve_off, auto, effective = self._get_valve_context(CONF_TANK_LEAK_AUTO_SHUTOFF)
        can_trigger = not self._cooldown_until or now >= self._cooldown_until
        
        # Track if we're transitioning from OFF to ON for auto-shutoff
        transitioning_on = False
        
        if can_trigger and similar_count >= self._repeat:
            prev_was_off = not self._attr_is_on
            self._attr_is_on = True
            transitioning_on = prev_was_off  # Store transition state for auto-shutoff
            # Capture synthetic flow at trigger time for notifications
            # For tank refill detection, use synthetic volume from the triggering session
            if prev_was_off:
                # Convert synthetic volume to equivalent flow rate (gallons/minute)
                if dur > 0 and synthetic_vol > 0:
                    self._synthetic_flow_at_trigger = (synthetic_vol * 60.0) / dur  # gallons/minute
                else:
                    self._synthetic_flow_at_trigger = 0.0
        else:
            # Auto-clear after idle period since last event
            if self._attr_is_on and self._last_event_ts and (now - self._last_event_ts).total_seconds() >= self._clear_idle_s:
                if not valve_off:
                    self._attr_is_on = False
                    if self._cooldown_s > 0:
                        self._cooldown_until = now + timedelta(seconds=self._cooldown_s)
        
        # Calculate timeout_cleared_s (how long since pattern broke when off)
        timeout_cleared_s = 0
        if not self._attr_is_on and self._last_event_ts:
            timeout_cleared_s = int((now - self._last_event_ts).total_seconds())
        
        # Build human-readable trigger reason
        if self._attr_is_on:
            latest_vol = self._history[-1][1] if self._history else 0
            if similar_count == 0:
                # This can happen when the history has been purged but the idle
                # timeout has not yet fired; explain that we're simply waiting
                # for the clear_idle timer rather than claiming a zero‑event
                # pattern.
                trigger_reason = (
                    "Pattern previously detected; awaiting clear_idle timeout"
                )
            else:
                trigger_reason = f"{similar_count} similar refills ({latest_vol:.1f}gal ±{self._tol_pct}%) detected in {self._window_s//60}min window"
        else:
            if similar_count > 0:
                trigger_reason = f"Only {similar_count} of {self._repeat} required similar refills detected"
            else:
                trigger_reason = "No tank refill pattern detected"

        # Get current flow and hot water percentage from tracker for universal attributes
        try:
            tracker_data = self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id, {})
            current_flow = float(tracker_data.get("flow_sensor_value", 0.0) or 0.0)
            hot_pct = float(tracker_data.get("current_session_hot_water_pct", 0.0) or 0.0)
        except Exception:
            current_flow = 0.0
            hot_pct = 0.0
        
        # For tank refill, elapsed_s represents time since pattern started
        elapsed_s = 0
        if self._history and len(self._history) > 0:
            elapsed_s = int((now - self._history[0][0]).total_seconds())

        self._attr_extra_state_attributes = {
            # Universal attributes
            "leak_type": "tank_refill",
            "trigger_reason": trigger_reason,
            "current_flow": round(current_flow, 2),
            "elapsed_s": elapsed_s,
            "trigger_value": similar_count,
            "trigger_threshold": self._repeat,
            "trigger_unit": "volume",
            "hot_pct": round(hot_pct, 1),
            
            # Tank refill specific attributes
            "similar_count": similar_count,
            "window_s": self._window_s,
            "contributing_events": contributing,
            "timeout_cleared_s": timeout_cleared_s,
            
            # Configuration attributes (kept for understanding)
            "events_in_window": len(self._history),
            "min_refill_volume": self._min_volume,
            "max_refill_volume": self._max_volume,
            "tolerance_pct": self._tol_pct,
            "repeat_count": self._repeat,
            "clear_idle_s": self._clear_idle_s,
            "cooldown_s": self._cooldown_s,
            "last_event": self._last_event_ts.isoformat() if self._last_event_ts else None,
            "min_refill_duration_s": self._min_duration_s,
            "max_refill_duration_s": self._max_duration_s,
            
            # Auto-shutoff attributes
            "auto_shutoff_on_trigger": auto,
            "auto_shutoff_effective": effective,
            "auto_shutoff_valve_entity": (valve_ent or None),
            "valve_off": valve_off,
            "synthetic_flow_at_trigger": self._synthetic_flow_at_trigger,
        }

        # Auto-shutoff action when transitioning OFF->ON
        if transitioning_on and effective and valve_ent:
            self._async_call_valve_off(valve_ent)
        # Write to the recorder when the on/off state changes, or when the
        # diagnostic attributes change (e.g. contributing_events/elapsed_s
        # progressing while still counting toward a trigger).
        if prev_on != self._attr_is_on or self._attr_extra_state_attributes != prev_attrs:
            self.async_write_ha_state()
