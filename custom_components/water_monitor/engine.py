from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from statistics import mean, pstdev
from typing import Any, Dict, List, Optional, Tuple

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.storage import Store

from .const import (
    DOMAIN,
    CONF_INTEL_DETECT_ENABLE,
    CONF_INTEL_LEARNING_ENABLE,
    CONF_OCC_MODE_ENTITY,
    CONF_OCC_STATE_AWAY,
    CONF_OCC_STATE_VACATION,
    engine_signal,
)

_LOGGER = logging.getLogger(__name__)


def _day_key(dt: datetime) -> str:
    # Use local date string YYYY-MM-DD for daily buckets
    return dt.astimezone().strftime("%Y-%m-%d")


@dataclass
class SessionRecord:
    ended_at: str  # ISO8601
    volume: float
    duration_s: int
    avg_flow: float
    hot_pct: float
    gaps: int = 0


@dataclass
class EngineState:
    sessions: List[SessionRecord] = field(default_factory=list)
    daily: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # Simple long-horizon stats for duration/flow by (hour, day_type)
    # Structure: {"H|D": {"durations": [int], "flows": [float], "count": int, "last_updated": iso}}
    hourly_stats: Dict[str, Dict[str, Any]] = field(default_factory=dict)


class WaterMonitorEngine:
    """
    Captures session summaries and performs a daily analysis to derive simple
    anomalies and usage stats. Results are persisted in .storage.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        config: Dict[str, Any],
    ) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self._config = config
        self._store: Store[dict] = Store(hass, 1, f"{DOMAIN}_{entry_id}_engine.json")
        self._state = EngineState()
        self._last_session_sig: Optional[tuple[float, int]] = None
        self._daily_unsub = None

    async def start(self) -> None:
        await self._load()
        # Schedule daily analysis at 03:10 local time
        if self._daily_unsub is None:
            self._daily_unsub = async_track_time_change(
                self.hass, self._handle_daily_tick, hour=3, minute=10, second=0
            )

    async def stop(self) -> None:
        if self._daily_unsub is not None:
            self._daily_unsub()
            self._daily_unsub = None
        await self._save()

    async def _load(self) -> None:
        try:
            data = await self._store.async_load()
            if not data:
                return
            sessions = [SessionRecord(**rec) for rec in data.get("sessions", [])]
            self._state.sessions = sessions
            self._state.daily = data.get("daily", {})
            self._state.hourly_stats = data.get("hourly_stats", {})
        except Exception as e:
            _LOGGER.warning("Engine state load failed: %s", e)

    async def _save(self) -> None:
        try:
            data = {
                "sessions": [rec.__dict__ for rec in self._state.sessions],
                "daily": self._state.daily,
                "hourly_stats": self._state.hourly_stats,
            }
            await self._store.async_save(data)
        except Exception as e:
            _LOGGER.warning("Engine state save failed: %s", e)

    @callback
    async def ingest_state(self, state: Dict[str, Any]) -> None:
        """
        Ingest tracker state_data. If a new last_session_* has appeared, record it.
        """
        # Only run when intelligent detection is toggled on, else just collect sessions.
        # Collection is lightweight; always enabled.
        vol = float(state.get("last_session_volume", 0.0) or 0.0)
        dur = int(state.get("last_session_duration", 0) or 0)
        if vol <= 0 or dur <= 0:
            return

        sig = (round(vol, 4), int(dur))
        if sig == self._last_session_sig:
            return  # already recorded

        self._last_session_sig = sig

        avg = float(state.get("last_session_average_flow", 0.0) or 0.0)
        hot_pct = float(state.get("last_session_hot_water_pct", 0.0) or 0.0)
        gaps = int(state.get("last_session_gapped_sessions", 0) or 0)
        ended_at = datetime.now(timezone.utc).isoformat()

        rec = SessionRecord(
            ended_at=ended_at,
            volume=round(vol, 6),
            duration_s=int(dur),
            avg_flow=round(avg, 6),
            hot_pct=round(hot_pct, 2),
            gaps=gaps,
        )
        self._state.sessions.append(rec)
        # Keep storage bounded (e.g., last 180 days sessions)
        if len(self._state.sessions) > 5000:
            self._state.sessions = self._state.sessions[-5000:]
        # Update hourly/context stats (first pass: hour + day_type only)
        try:
            dt = datetime.fromisoformat(ended_at)
        except Exception:
            dt = datetime.now(timezone.utc)
        hour, day_type = self._local_hour_and_daytype(dt)
        self._update_hourly_stats(hour, day_type, rec.duration_s, rec.avg_flow)
        await self._save()
        # Notify interested entities
        async_dispatcher_send(self.hass, engine_signal(self.entry_id), {
            "type": "ingest",
            "event": "session_recorded",
            "record": rec.__dict__,
        })

    async def analyze_yesterday(self) -> Dict[str, Any]:
        """Compute daily summary for yesterday and store it."""
        now_local = datetime.now().astimezone()
        y_local = (now_local - timedelta(days=1)).replace(hour=23, minute=59, second=59, microsecond=0)
        y_key = _day_key(y_local)

        # Sessions are stored with UTC timestamps; convert to local day bucket
        day_sessions: List[SessionRecord] = []
        for rec in self._state.sessions:
            try:
                dt = datetime.fromisoformat(rec.ended_at)
            except Exception:
                continue
            if _day_key(dt) == y_key:
                day_sessions.append(rec)

        total_vol = sum(s.volume for s in day_sessions)
        session_count = len(day_sessions)
        avg_dur = mean([s.duration_s for s in day_sessions]) if day_sessions else 0.0
        avg_hot = mean([s.hot_pct for s in day_sessions]) if day_sessions else 0.0

        # Baseline: last 7 days (excluding yesterday)
        last7: List[float] = []
        for d in range(2, 9):
            dk = _day_key(now_local - timedelta(days=d))
            prev = self._state.daily.get(dk)
            if prev:
                last7.append(float(prev.get("total_volume", 0.0) or 0.0))

        baseline_mean = mean(last7) if last7 else 0.0
        baseline_std = pstdev(last7) if len(last7) >= 2 else 0.0
        threshold = baseline_mean + 3 * baseline_std if baseline_std > 0 else None
        anomaly = bool(threshold is not None and total_vol > threshold)

        summary = {
            "date": y_key,
            "total_volume": round(total_vol, 3),
            "sessions": session_count,
            "avg_duration_s": round(avg_dur, 1),
            "avg_hot_pct": round(avg_hot, 1),
            "baseline_mean": round(baseline_mean, 3),
            "baseline_std": round(baseline_std, 3),
            "threshold_3sigma": round(threshold, 3) if threshold is not None else None,
            "anomaly": anomaly,
        }

        self._state.daily[y_key] = summary
        # Keep daily map reasonable (e.g., 365 days)
        if len(self._state.daily) > 370:
            # prune oldest
            for dk in sorted(self._state.daily.keys())[:-370]:
                self._state.daily.pop(dk, None)
        await self._save()

        _LOGGER.info("Daily water summary for %s: %s", y_key, summary)
        # Notify interested entities
        async_dispatcher_send(self.hass, engine_signal(self.entry_id), {
            "type": "daily",
            "event": "summary",
            "summary": summary,
        })
        return summary

    async def _handle_daily_tick(self, *_args) -> None:
        # Only run analysis when intelligent detection is enabled; still safe if disabled
        enabled = bool(self._config.get(CONF_INTEL_DETECT_ENABLE, False))
        if not enabled:
            return
        try:
            await self.analyze_yesterday()
        except Exception as e:
            _LOGGER.exception("Daily analysis failed: %s", e)

    # -------------------------
    # Baselines (simple version)
    # -------------------------

    def _local_hour_and_daytype(self, dt: datetime) -> Tuple[int, str]:
        try:
            local = dt.astimezone()
        except Exception:
            local = datetime.now().astimezone()
        hour = local.hour
        day_type = "weekend" if local.weekday() >= 5 else "weekday"
        return hour, day_type

    def _bucket_key(self, hour: int, day_type: str) -> str:
        return f"{hour}|{day_type}"

    def _update_hourly_stats(self, hour: int, day_type: str, duration_s: int, avg_flow: float) -> None:
        key = self._bucket_key(hour, day_type)
        slot = self._state.hourly_stats.setdefault(key, {"durations": [], "flows": [], "count": 0, "last_updated": None})
        # Append and bound arrays for simple online storage; size cap per bucket ~200
        slot["durations"].append(int(duration_s))
        if len(slot["durations"]) > 200:
            slot["durations"] = slot["durations"][-200:]
        try:
            f = float(avg_flow)
        except Exception:
            f = 0.0
        slot["flows"].append(f)
        if len(slot["flows"]) > 200:
            slot["flows"] = slot["flows"][-200:]
        slot["count"] = len(slot["durations"])  # simple count
        slot["last_updated"] = datetime.now(timezone.utc).isoformat()

    def _percentile(self, data: List[float] | List[int], pct: float) -> float:
        if not data:
            return 0.0
        xs = sorted(float(x) for x in data)
        if len(xs) == 1:
            return xs[0]
        # Clamp percentile to [0, 100]
        p = max(0.0, min(100.0, pct)) / 100.0
        idx = p * (len(xs) - 1)
        lo = int(idx)
        hi = min(lo + 1, len(xs) - 1)
        frac = idx - lo
        return xs[lo] * (1.0 - frac) + xs[hi] * frac

    def get_simple_bucket_stats(self, hour: int, day_type: str) -> Dict[str, Any]:
        """Return simple stats for a bucket and fallbacks if needed.

        Fallbacks: (hour, day_type) -> (hour) -> global
        """
        def make_stats(durations: List[int]) -> Dict[str, Any]:
            return {
                "count": len(durations),
                "p50": round(self._percentile(durations, 50), 1),
                "p90": round(self._percentile(durations, 90), 1),
                "p95": round(self._percentile(durations, 95), 1),
                "p99": round(self._percentile(durations, 99), 1),
            }

        # Exact bucket
        key = self._bucket_key(hour, day_type)
        slot = self._state.hourly_stats.get(key)
        if slot and slot.get("durations"):
            durs = [int(x) for x in slot["durations"] if isinstance(x, (int, float))]
            st = make_stats(durs)
            st["bucket"] = key
            st["level"] = 1
            return st

        # Hour-only fallback (merge weekday+weekend for this hour)
        merged: List[int] = []
        for dtp in ("weekday", "weekend"):
            s = self._state.hourly_stats.get(self._bucket_key(hour, dtp))
            if s and s.get("durations"):
                merged.extend(int(x) for x in s["durations"] if isinstance(x, (int, float)))
        if merged:
            st = make_stats(merged)
            st["bucket"] = f"{hour}|*"
            st["level"] = 2
            return st

        # Global fallback: merge everything
        all_durs: List[int] = []
        for s in self._state.hourly_stats.values():
            if s and s.get("durations"):
                all_durs.extend(int(x) for x in s["durations"] if isinstance(x, (int, float)))
        st = make_stats(all_durs)
        st["bucket"] = "*"
        st["level"] = 3
        return st
