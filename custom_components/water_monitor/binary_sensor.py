from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Deque, List, Optional, Tuple

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
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
    UPDATE_INTERVAL,
    # base
    CONF_SENSOR_PREFIX,
    CONF_FLOW_SENSOR,
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
    DEFAULTS,
    # tank refill leak
    CONF_TANK_LEAK_ENABLE,
    CONF_TANK_LEAK_MIN_REFILL_VOLUME,
    CONF_TANK_LEAK_TOLERANCE_PCT,
    CONF_TANK_LEAK_REPEAT_COUNT,
    CONF_TANK_LEAK_WINDOW_S,
    CONF_TANK_LEAK_CLEAR_IDLE_S,
    CONF_TANK_LEAK_COOLDOWN_S,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    opts = {**entry.data, **entry.options}
    entities: List[BinarySensorEntity] = []

    # Low-flow leak
    if bool(opts.get(CONF_LOW_FLOW_ENABLE, DEFAULTS[CONF_LOW_FLOW_ENABLE])):
        flow_sensor = opts.get(CONF_FLOW_SENSOR)
        if flow_sensor:
            prefix = opts.get(CONF_SENSOR_PREFIX) or "Water Monitor"
            entities.append(
                LowFlowLeakBinarySensor(entry=entry, name=f"{prefix} Low-flow leak")
            )
        else:
            _LOGGER.warning(
                "Low-flow leak sensor enabled but no flow sensor configured; skipping entity creation."
            )

    # Tank refill leak
    if bool(opts.get(CONF_TANK_LEAK_ENABLE, DEFAULTS[CONF_TANK_LEAK_ENABLE])):
        prefix = opts.get(CONF_SENSOR_PREFIX) or "Water Monitor"
        entities.append(
            TankRefillLeakBinarySensor(entry=entry, name=f"{prefix} Tank refill leak")
        )

    if entities:
        async_add_entities(entities)


class LowFlowLeakBinarySensor(BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, entry: ConfigEntry, name: str) -> None:
        self._entry = entry
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_low_flow_leak"
        self._attr_is_on = False
        self._attr_available = True
        self._attr_extra_state_attributes = {}

        # Runtime
        self._flow_entity_id = entry.options.get(CONF_FLOW_SENSOR) or entry.data.get(
            CONF_FLOW_SENSOR
        )
        self._unit: Optional[str] = None  # keep but do NOT expose as unit_of_measurement

        # Config (resolved with defaults)
        ex = {**entry.data, **entry.options}
        self._max_flow = float(
            ex.get(CONF_LOW_FLOW_MAX_FLOW, DEFAULTS[CONF_LOW_FLOW_MAX_FLOW])
        )
        self._seed_s = int(ex.get(CONF_LOW_FLOW_SEED_S, DEFAULTS[CONF_LOW_FLOW_SEED_S]))
        self._min_s = int(ex.get(CONF_LOW_FLOW_MIN_S, DEFAULTS[CONF_LOW_FLOW_MIN_S]))
        self._clear_idle_s = int(
            ex.get(CONF_LOW_FLOW_CLEAR_IDLE_S, DEFAULTS[CONF_LOW_FLOW_CLEAR_IDLE_S])
        )
        self._counting_mode = ex.get(
            CONF_LOW_FLOW_COUNTING_MODE, DEFAULTS[CONF_LOW_FLOW_COUNTING_MODE]
        )
        self._smoothing_s = int(
            ex.get(CONF_LOW_FLOW_SMOOTHING_S, DEFAULTS[CONF_LOW_FLOW_SMOOTHING_S])
        )
        self._cooldown_s = int(
            ex.get(CONF_LOW_FLOW_COOLDOWN_S, DEFAULTS[CONF_LOW_FLOW_COOLDOWN_S])
        )
        self._clear_on_high_s = ex.get(
            CONF_LOW_FLOW_CLEAR_ON_HIGH_S, DEFAULTS[CONF_LOW_FLOW_CLEAR_ON_HIGH_S]
        )

        # State machine
        self._stage = "idle"  # idle | seeding | counting | triggered
        self._elapsed_seed = 0
        self._elapsed_leak = 0
        self._last_update: Optional[datetime] = None
        # Clear logic helpers
        self._zero_since: Optional[datetime] = None
        self._high_since: Optional[datetime] = None
        self._cooldown_until: Optional[datetime] = None
        self._last_triggered: Optional[datetime] = None
        self._last_state_change: Optional[datetime] = None

        # Flow buffering for smoothing
        self._samples: Deque[Tuple[datetime, float]] = deque(maxlen=1200)

        # Subscriptions
        self._unsub_state = None
        self._unsub_interval = None

    @property
    def device_info(self) -> DeviceInfo:
        # Same device as sensors: name=prefix, model/manufacturer fixed
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
        self._attr_is_on = False
        self._attr_available = True
        self._last_state_change = datetime.now(timezone.utc)
        self.async_write_ha_state()

        # Subscribe to flow sensor changes
        if self._flow_entity_id:
            self._unsub_state = async_track_state_change_event(
                self.hass, [self._flow_entity_id], self._async_sensor_changed
            )

        # Ensure timer runs so durations progress even without sensor events
        self._unsub_interval = async_track_time_interval(
            self.hass, self._async_periodic_update, timedelta(seconds=UPDATE_INTERVAL)
        )

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_state:
            self._unsub_state()
            self._unsub_state = None
        if self._unsub_interval:
            self._unsub_interval()
            self._unsub_interval = None
        await super().async_will_remove_from_hass()

    @callback
    async def _async_periodic_update(self, now: datetime) -> None:
        await self._evaluate(now)

    async def _async_sensor_changed(self, event) -> None:
        await self._evaluate(datetime.now(timezone.utc))

    def _ingest_flow(self, now: datetime) -> float:
        """Read raw flow and update smoothing buffer."""
        st = self.hass.states.get(self._flow_entity_id)
        raw_flow = 0.0
        if st and st.state not in (None, "unknown", "unavailable"):
            try:
                raw_flow = max(0.0, float(st.state))
                # fetch unit from flow sensor (do not expose as unit_of_measurement)
                self._unit = st.attributes.get("unit_of_measurement", self._unit)
            except (ValueError, TypeError):
                raw_flow = 0.0

        # Add to samples
        self._samples.append((now, raw_flow))

        # Compute smoothed
        if self._smoothing_s <= 0:
            return raw_flow

        cutoff = now - timedelta(seconds=self._smoothing_s)
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()

        if not self._samples:
            return raw_flow

        # time-weighted average over window
        total_time = 0.0
        area = 0.0
        prev_t, prev_v = self._samples[0]
        for t, v in list(self._samples)[1:]:
            dt = (t - prev_t).total_seconds()
            if dt > 0:
                total_time += dt
                area += prev_v * dt
            prev_t, prev_v = t, v
        # Include up to now
        dt = (now - prev_t).total_seconds()
        if dt > 0:
            total_time += dt
            area += prev_v * dt

        return (area / total_time) if total_time > 0 else raw_flow

    async def _evaluate(self, now: datetime) -> None:
        """Advance timers and state machine."""
        suppressed_reason = (
            "cooldown" if (self._cooldown_until and now < self._cooldown_until) else None
        )
        smoothed = self._ingest_flow(now)

        # Delta time
        if self._last_update is None:
            self._last_update = now
            delta = 0
        else:
            delta = max(0, int((now - self._last_update).total_seconds()))
            self._last_update = now

        # Zero detection for clear logic
        if smoothed <= 0.0:
            if self._zero_since is None:
                self._zero_since = now
            zero_elapsed = (now - self._zero_since).total_seconds() if self._zero_since else 0
        else:
            self._zero_since = None
            zero_elapsed = 0

        # Optional sustained high-flow clear
        if smoothed > self._max_flow:
            if self._high_since is None:
                self._high_since = now
        else:
            self._high_since = None

        # Apply state machine
        prev_stage = self._stage
        prev_on = self._attr_is_on

        if suppressed_reason == "cooldown":
            # During cooldown, we don't change state, but we still clear if zero persists (safety)
            if self._attr_is_on and self._zero_since and zero_elapsed >= self._clear_idle_s:
                self._attr_is_on = False
                self._stage = "idle"
                self._elapsed_seed = 0
                self._elapsed_leak = 0
                self._last_state_change = now
        else:
            # Normal behavior
            if self._stage == "idle":
                if smoothed > 0.0:
                    self._stage = "seeding"
                    self._elapsed_seed = 0

            elif self._stage == "seeding":
                if smoothed <= 0.0:
                    self._stage = "idle"
                    self._elapsed_seed = 0
                elif smoothed <= self._max_flow:
                    self._elapsed_seed += delta
                    if self._elapsed_seed >= self._seed_s:
                        self._stage = "counting"
                        self._elapsed_leak = 0
                # else: above threshold, hold stage

            elif self._stage == "counting":
                if smoothed <= 0.0 and self._zero_since and zero_elapsed >= self._clear_idle_s:
                    self._stage = "idle"
                    self._elapsed_seed = 0
                    self._elapsed_leak = 0
                else:
                    if self._counting_mode == COUNTING_MODE_NONZERO:
                        if smoothed > 0.0:
                            self._elapsed_leak += delta
                    else:  # in_range_only
                        if 0.0 < smoothed <= self._max_flow:
                            self._elapsed_leak += delta

                    if self._elapsed_leak >= self._min_s:
                        self._stage = "triggered"
                        self._attr_is_on = True
                        self._last_triggered = now
                        self._last_state_change = now

            elif self._stage == "triggered":
                if smoothed <= 0.0 and self._zero_since and zero_elapsed >= self._clear_idle_s:
                    self._attr_is_on = False
                    self._stage = "idle"
                    self._elapsed_seed = 0
                    self._elapsed_leak = 0
                    self._last_state_change = now
                    if self._cooldown_s > 0:
                        self._cooldown_until = now + timedelta(seconds=self._cooldown_s)
                else:
                    if self._clear_on_high_s and self._high_since:
                        if (now - self._high_since).total_seconds() >= float(
                            self._clear_on_high_s
                        ):
                            self._attr_is_on = False
                            self._stage = "idle"
                            self._elapsed_seed = 0
                            self._elapsed_leak = 0
                            self._last_state_change = now
                            if self._cooldown_s > 0:
                                self._cooldown_until = now + timedelta(
                                    seconds=self._cooldown_s
                                )

        # Attributes (no unit_of_measurement on binary sensor)
        self._attr_extra_state_attributes = {
            "stage": self._stage,
            "current_flow": round(float(smoothed), 3),
            "flow_unit": self._unit,
            "max_leak_flow": self._max_flow,
            "seed_low_flow_duration_s": self._seed_s,
            "min_duration_s": self._min_s,
            "clear_idle_s": self._clear_idle_s,
            "counting_mode": self._counting_mode,
            "smoothing_window_s": self._smoothing_s,
            "cooldown_s": self._cooldown_s,
            "clear_on_sustained_high_flow_s": self._clear_on_high_s,
            "elapsed_seed_s": self._elapsed_seed,
            "elapsed_leak_s": self._elapsed_leak,
            "last_triggered": self._last_triggered.isoformat()
            if self._last_triggered
            else None,
            "last_state_change": self._last_state_change.isoformat()
            if self._last_state_change
            else None,
        }

        if prev_on != self._attr_is_on or prev_stage != self._stage:
            self.async_write_ha_state()


class TankRefillLeakBinarySensor(BinarySensorEntity):
    """Detect repeated similar refill sessions in a short window (e.g., toilet flapper leaks)."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, entry: ConfigEntry, name: str) -> None:
        self._entry = entry
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_tank_refill_leak"
        self._attr_is_on = False
        self._attr_available = True
        self._attr_extra_state_attributes = {}

        ex = {**entry.data, **entry.options}
        self._min_volume = float(
            ex.get(CONF_TANK_LEAK_MIN_REFILL_VOLUME, DEFAULTS[CONF_TANK_LEAK_MIN_REFILL_VOLUME])
        )
        self._tol_pct = float(
            ex.get(CONF_TANK_LEAK_TOLERANCE_PCT, DEFAULTS[CONF_TANK_LEAK_TOLERANCE_PCT])
        )
        self._repeat = int(
            ex.get(CONF_TANK_LEAK_REPEAT_COUNT, DEFAULTS[CONF_TANK_LEAK_REPEAT_COUNT])
        )
        self._window_s = int(ex.get(CONF_TANK_LEAK_WINDOW_S, DEFAULTS[CONF_TANK_LEAK_WINDOW_S]))
        self._clear_idle_s = int(
            ex.get(CONF_TANK_LEAK_CLEAR_IDLE_S, DEFAULTS[CONF_TANK_LEAK_CLEAR_IDLE_S])
        )
        self._cooldown_s = int(
            ex.get(CONF_TANK_LEAK_COOLDOWN_S, DEFAULTS[CONF_TANK_LEAK_COOLDOWN_S])
        )

        # Refill history (timestamp, volume)
        self._history: Deque[Tuple[datetime, float]] = deque(maxlen=100)
        self._last_event: Optional[datetime] = None
        self._cooldown_until: Optional[datetime] = None
        # Upstream last_session entity and subscription
        self._source_entity_id: Optional[str] = None
        self._unsub_state = None

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
        self._attr_is_on = False
        self._attr_available = True
        self.async_write_ha_state()
        # Resolve and subscribe to last_session changes
        await self._resolve_and_subscribe()

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_state:
            self._unsub_state()
            self._unsub_state = None
        await super().async_will_remove_from_hass()

    @callback
    async def _async_source_changed(self, event) -> None:
        await self._evaluate(datetime.now(timezone.utc))

    async def _resolve_and_subscribe(self) -> None:
        """Find the last_session sensor's entity_id and subscribe to its changes."""
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
            from homeassistant.helpers.event import async_call_later

            async_call_later(
                self.hass, 2.0, lambda _: self.hass.async_create_task(self._resolve_and_subscribe())
            )
            return
        self._source_entity_id = entity.entity_id
        self._unsub_state = async_track_state_change_event(
            self.hass, [self._source_entity_id], self._async_source_changed
        )

    async def _evaluate(self, now: datetime) -> None:
        # Cooldown suppress
        if self._cooldown_until and now < self._cooldown_until:
            return

        # Read last completed session metrics from the last_session sensor's attributes
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

        # If this volume changed since last tick and exceeds minimum, consider it a new refill event
        changed = False
        prev_vol = self._history[-1][1] if self._history else None
        if prev_vol is None or abs(vol - prev_vol) > 1e-6:
            changed = True

        if changed and vol >= self._min_volume:
            self._history.append((now, vol))
            self._last_event = now

        # Purge outside window
        cutoff = now - timedelta(seconds=self._window_s)
        while self._history and self._history[0][0] < cutoff:
            self._history.popleft()

        # Group by similarity: count how many in the last window fall within tolerance of the latest
        if self._history:
            latest_vol = self._history[-1][1]
            tol = latest_vol * (self._tol_pct / 100.0)
            lo, hi = latest_vol - tol, latest_vol + tol
            count = sum(1 for (_, v) in self._history if lo <= v <= hi)
        else:
            count = 0

        prev_is_on = self._attr_is_on
        if count >= self._repeat:
            self._attr_is_on = True
        else:
            # Auto-clear if no matching refills for clear_idle_s
            if self._attr_is_on and self._last_event and (
                now - self._last_event
            ).total_seconds() >= self._clear_idle_s:
                self._attr_is_on = False
                if self._cooldown_s > 0:
                    self._cooldown_until = now + timedelta(seconds=self._cooldown_s)

        self._attr_extra_state_attributes = {
            "events_in_window": len(self._history),
            "similar_count": count,
            "min_refill_volume": self._min_volume,
            "tolerance_pct": self._tol_pct,
            "repeat_count": self._repeat,
            "window_s": self._window_s,
            "clear_idle_s": self._clear_idle_s,
            "cooldown_s": self._cooldown_s,
            "last_event": self._last_event.isoformat() if self._last_event else None,
        }

        if prev_is_on != self._attr_is_on:
            self.async_write_ha_state()
