from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Deque, Optional, Tuple

from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval

from .const import (
    DOMAIN,
    UPDATE_INTERVAL,
    # base config keys
    CONF_SENSOR_PREFIX,
    CONF_FLOW_SENSOR,
    # low-flow keys
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
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    opts = {**entry.data, **entry.options}
    enabled = bool(opts.get(CONF_LOW_FLOW_ENABLE, DEFAULTS[CONF_LOW_FLOW_ENABLE]))
    if not enabled:
        return

    flow_sensor = opts.get(CONF_FLOW_SENSOR)
    if not flow_sensor:
        _LOGGER.warning("Low-flow leak sensor enabled but no flow sensor configured; skipping entity creation.")
        return

    prefix = opts.get(CONF_SENSOR_PREFIX) or "Water Monitor"

    entity = LowFlowLeakBinarySensor(entry=entry, name=f"{prefix} Low-flow leak")
    async_add_entities([entity])


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
        self._flow_entity_id: str = (entry.options.get(CONF_FLOW_SENSOR) or entry.data.get(CONF_FLOW_SENSOR))
        self._unit: Optional[str] = None  # keep but do NOT expose as unit_of_measurement

        # Config (resolved with defaults)
        ex = {**entry.data, **entry.options}
        self._max_flow = float(ex.get(CONF_LOW_FLOW_MAX_FLOW, DEFAULTS[CONF_LOW_FLOW_MAX_FLOW]))
        self._seed_s = int(ex.get(CONF_LOW_FLOW_SEED_S, DEFAULTS[CONF_LOW_FLOW_SEED_S]))
        self._min_s = int(ex.get(CONF_LOW_FLOW_MIN_S, DEFAULTS[CONF_LOW_FLOW_MIN_S]))
        self._clear_idle_s = int(ex.get(CONF_LOW_FLOW_CLEAR_IDLE_S, DEFAULTS[CONF_LOW_FLOW_CLEAR_IDLE_S]))
        self._counting_mode = ex.get(CONF_LOW_FLOW_COUNTING_MODE, DEFAULTS[CONF_LOW_FLOW_COUNTING_MODE])
        self._smoothing_s = int(ex.get(CONF_LOW_FLOW_SMOOTHING_S, DEFAULTS[CONF_LOW_FLOW_SMOOTHING_S]))
        self._cooldown_s = int(ex.get(CONF_LOW_FLOW_COOLDOWN_S, DEFAULTS[CONF_LOW_FLOW_COOLDOWN_S]))
        self._clear_on_high_s = ex.get(CONF_LOW_FLOW_CLEAR_ON_HIGH_S, DEFAULTS[CONF_LOW_FLOW_CLEAR_ON_HIGH_S])

        # State machine
        self._stage = "idle"  # idle | seeding | counting | triggered
        self._elapsed_seed = 0
        self._elapsed_leak = 0
        self._last_update: Optional[datetime] = None
        self._zero_since: Optional[datetime] = None
        self._high_since: Optional[datetime] = None
        self._cooldown_until: Optional[datetime] = None
        self._last_triggered: Optional[datetime] = None
        self._last_state_change: Optional[datetime] = None

        # Flow buffering for smoothing
        self._samples: Deque[Tuple[datetime, float]] = deque(maxlen=1200)  # up to ~20 minutes at 1s

        # Subscriptions
        self._unsub_state = None
        self._unsub_interval = None

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name=self._entry.title or "Water Monitor",
            manufacturer="Water Monitor",
            model="Low-flow Leak Detector",
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
                self.hass,
                [self._flow_entity_id],
                self._async_sensor_changed,
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
        if self._cooldown_until and now < self._cooldown_until:
            suppressed_reason = "cooldown"
        else:
            suppressed_reason = None

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
                # else remain idle

            elif self._stage == "seeding":
                if smoothed <= 0.0:
                    # reset if flow stops
                    self._stage = "idle"
                    self._elapsed_seed = 0
                elif smoothed <= self._max_flow:
                    self._elapsed_seed += delta
                    if self._elapsed_seed >= self._seed_s:
                        self._stage = "counting"
                        self._elapsed_leak = 0
                else:
                    # above low-flow threshold, hold stage, do not accumulate
                    pass

            elif self._stage == "counting":
                if smoothed <= 0.0 and self._zero_since and zero_elapsed >= self._clear_idle_s:
                    # flow stopped long enough -> reset
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
                    # clear on true stop
                    self._attr_is_on = False
                    self._stage = "idle"
                    self._elapsed_seed = 0
                    self._elapsed_leak = 0
                    self._last_state_change = now
                    if self._cooldown_s > 0:
                        self._cooldown_until = now + timedelta(seconds=self._cooldown_s)
                else:
                    # optional clear on sustained high-flow (disabled by default)
                    if self._clear_on_high_s and self._high_since:
                        if (now - self._high_since).total_seconds() >= float(self._clear_on_high_s):
                            self._attr_is_on = False
                            self._stage = "idle"
                            self._elapsed_seed = 0
                            self._elapsed_leak = 0
                            self._last_state_change = now
                            if self._cooldown_s > 0:
                                self._cooldown_until = now + timedelta(seconds=self._cooldown_s)
                    # remain triggered otherwise

        # Attributes (do NOT expose unit_of_measurement on binary sensors)
        self._attr_extra_state_attributes = {
            "stage": self._stage,
            "current_flow": round(float(smoothed), 3),
            "flow_unit": self._unit,  # renamed from unit_of_measurement
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
            "last_triggered": self._last_triggered.isoformat() if self._last_triggered else None,
            "last_state_change": self._last_state_change.isoformat() if self._last_state_change else None,
        }

        # Only write state when something meaningful changed
        if prev_on != self._attr_is_on or prev_stage != self._stage:
            self.async_write_ha_state()