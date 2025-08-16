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
    # Context at (approx) end time
    occ_raw: Optional[str] = None
    occ_class: Optional[str] = None  # home | away | vacation | night
    people_bin: Optional[str] = None  # '0' | '1' | '2-3' | '4+'


@dataclass
class EngineState:
    sessions: List[SessionRecord] = field(default_factory=list)
    daily: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # Simple long-horizon stats for duration/flow by (hour, day_type)
    # Structure: {"H|D": {"durations": [int], "flows": [float], "count": int, "last_updated": iso}}
    hourly_stats: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # Context-aware stats: (hour|day_type|occ_class|people_bin)
    context_stats: Dict[str, Dict[str, Any]] = field(default_factory=dict)


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
            self._state.context_stats = data.get("context_stats", {})
        except Exception as e:
            _LOGGER.warning("Engine state load failed: %s", e)

    async def _save(self) -> None:
        try:
            data = {
                "sessions": [rec.__dict__ for rec in self._state.sessions],
                "daily": self._state.daily,
                "hourly_stats": self._state.hourly_stats,
                "context_stats": self._state.context_stats,
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
        # Exclude synthetic contribution from engine stats/analysis
        synth = float(state.get("last_session_synthetic_volume", 0.0) or 0.0)
        vol_eff = max(0.0, vol - max(0.0, synth))
        if vol_eff <= 0 or dur <= 0:
            return

        sig = (round(vol_eff, 4), int(dur))
        if sig == self._last_session_sig:
            return  # already recorded

        self._last_session_sig = sig

        avg = float(state.get("last_session_average_flow", 0.0) or 0.0)
        # Adjust average flow to remove synthetic portion (volume/time)
        try:
            if dur > 0 and synth > 0:
                avg -= (synth / (dur / 60.0))
                if avg < 0:
                    avg = 0.0
        except Exception:
            pass
        hot_pct = float(state.get("last_session_hot_water_pct", 0.0) or 0.0)
        gaps = int(state.get("last_session_gapped_sessions", 0) or 0)

        now_utc = datetime.now(timezone.utc)
        ended_at = now_utc.isoformat()

        # Classify context at end time
        occ_raw, occ_class, people_bin = self._classify_context(now_utc)

        rec = SessionRecord(
            ended_at=ended_at,
            volume=round(vol_eff, 6),
            duration_s=int(dur),
            avg_flow=round(avg, 6),
            hot_pct=round(hot_pct, 2),
            gaps=gaps,
            occ_raw=occ_raw,
            occ_class=occ_class,
            people_bin=people_bin,
        )
        self._state.sessions.append(rec)
        # Keep storage bounded (e.g., last 180 days sessions)
        if len(self._state.sessions) > 5000:
            self._state.sessions = self._state.sessions[-5000:]
        # Update hourly/context stats (first pass: hour + day_type only)
        try:
            dt = datetime.fromisoformat(ended_at)
        except Exception:
            dt = now_utc
        hour, day_type = self._local_hour_and_daytype(dt)
        self._update_hourly_stats(hour, day_type, rec.duration_s, rec.avg_flow)
        # Update context-aware stats
        self._update_context_stats(hour, day_type, occ_class, people_bin, rec.duration_s, rec.avg_flow)
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

    def _ctx_key(self, hour: int, day_type: str, occ_class: str, people_bin: str) -> str:
        return f"{hour}|{day_type}|{occ_class}|{people_bin}"

    def _update_context_stats(
        self,
        hour: int,
        day_type: str,
        occ_class: Optional[str],
        people_bin: Optional[str],
        duration_s: int,
        avg_flow: float,
    ) -> None:
        oc = occ_class or "unknown"
        pb = people_bin or "?"
        key = self._ctx_key(hour, day_type, oc, pb)
        slot = self._state.context_stats.setdefault(key, {"durations": [], "flows": [], "count": 0, "last_updated": None})
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

    def get_context_bucket_stats(
        self,
        hour: int,
        day_type: str,
        occ_class: Optional[str],
        people_bin: Optional[str],
    ) -> Dict[str, Any]:
        """Return context-aware stats with fallback ladder.

        Ladder:
        1) hour|day_type|occ_class|people_bin
        2) hour|day_type|occ_class|*
        3) hour|day_type
        4) hour
        5) global
        """

        def make_stats(durations: List[int], bucket: str, level: int) -> Dict[str, Any]:
            return {
                "bucket": bucket,
                "level": level,
                "count": len(durations),
                "p50": round(self._percentile(durations, 50), 1),
                "p90": round(self._percentile(durations, 90), 1),
                "p95": round(self._percentile(durations, 95), 1),
                "p99": round(self._percentile(durations, 99), 1),
            }

        oc = (occ_class or "unknown")
        pb = (people_bin or "?")
        # 1: exact
        key = self._ctx_key(hour, day_type, oc, pb)
        slot = self._state.context_stats.get(key)
        if slot and slot.get("durations"):
            d = [int(x) for x in slot["durations"] if isinstance(x, (int, float))]
            return make_stats(d, key, 1)
        # 2: wildcard people_bin
        merged: List[int] = []
        for bin_opt in ("0", "1", "2-3", "4+", "?"):
            s = self._state.context_stats.get(self._ctx_key(hour, day_type, oc, bin_opt))
            if s and s.get("durations"):
                merged.extend(int(x) for x in s["durations"] if isinstance(x, (int, float)))
        if merged:
            return make_stats(merged, f"{hour}|{day_type}|{oc}|*", 2)
        # 3/4/5: fall back to simpler ladders
        st = self.get_simple_bucket_stats(hour, day_type)
        st["level"] = max(3, int(st.get("level", 3)))
        return st

    # -------------------------
    # Context classification helpers
    # -------------------------
    def _split_states(self, value: Optional[str]) -> List[str]:
        if not value:
            return []
        return [s.strip() for s in str(value).split(",") if s.strip()]

    def _classify_context(self, dt_utc: datetime) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """Return (occupancy_raw, occupancy_class, people_bin) for a given time."""
        try:
            local = dt_utc.astimezone()
        except Exception:
            local = datetime.now().astimezone()
        hour = local.hour

        occ_ent = str(self._config.get(CONF_OCC_MODE_ENTITY) or "")
        occ_raw = None
        if occ_ent:
            st = self.hass.states.get(occ_ent)
            if st and st.state not in (None, "unknown", "unavailable"):
                occ_raw = str(st.state)

        away_list = self._split_states(self._config.get(CONF_OCC_STATE_AWAY))
        vac_list = self._split_states(self._config.get(CONF_OCC_STATE_VACATION))
        if occ_raw and occ_raw in vac_list:
            occ_class = "vacation"
        elif occ_raw and occ_raw in away_list:
            occ_class = "away"
        else:
            # Simple night heuristic: 0-5 local hours considered night
            if 0 <= hour <= 5:
                occ_class = "night"
            else:
                occ_class = "home"

        # Count people.* at home
        people_home = 0
        try:
            for eid, st in self.hass.states.async_all("person"):
                pass  # incorrect API, fallback below
        except Exception:
            pass
        # Use states list in hass
        try:
            for st in self.hass.states.async_all():
                if st.domain == "person" and str(st.state).lower() == "home":
                    people_home += 1
        except Exception:
            # Very defensive
            pass

        if people_home <= 0:
            people_bin = "0"
        elif people_home == 1:
            people_bin = "1"
        elif 2 <= people_home <= 3:
            people_bin = "2-3"
        else:
            people_bin = "4+"

        return occ_raw, occ_class, people_bin

    def get_context_stats_for_now(self) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        hour, day_type = self._local_hour_and_daytype(now)
        occ_raw, occ_class, people_bin = self._classify_context(now)
        stats = self.get_context_bucket_stats(hour, day_type, occ_class, people_bin)
        stats.update({
            "occ_raw": occ_raw,
            "occ_class": occ_class,
            "people_bin": people_bin,
            "hour": hour,
            "day_type": day_type,
        })
        return stats

    # -------------------------
    # Simulation tooling (to accelerate baseline creation)
    # -------------------------
    async def simulate_history(self, days: int = 14, seed: Optional[int] = None, include_irrigation: bool = True) -> Dict[str, Any]:
        import random
        if seed is not None:
            random.seed(int(seed))
        created = 0
        now_local = datetime.now().astimezone()

        def add_session(dt_local: datetime, duration: int, avg_flow: float, hot_pct: float):
            nonlocal created
            dt_utc = dt_local.astimezone(timezone.utc)
            occ_raw, occ_class, people_bin = self._classify_context(dt_utc)
            rec = SessionRecord(
                ended_at=dt_utc.isoformat(),
                volume=round((avg_flow / 60.0) * max(1, duration), 3),
                duration_s=int(duration),
                avg_flow=float(avg_flow),
                hot_pct=round(hot_pct, 1),
                gaps=0,
                occ_raw=occ_raw,
                occ_class=occ_class,
                people_bin=people_bin,
            )
            self._state.sessions.append(rec)
            # Update stats
            h, dty = self._local_hour_and_daytype(dt_utc)
            self._update_hourly_stats(h, dty, rec.duration_s, rec.avg_flow)
            self._update_context_stats(h, dty, occ_class, people_bin, rec.duration_s, rec.avg_flow)
            created += 1

        for d in range(days, 0, -1):
            base_day = (now_local - timedelta(days=d)).replace(minute=0, second=0, microsecond=0)
            dow = base_day.weekday()
            # Morning showers (1-3)
            for _ in range(random.randint(1, 3)):
                hr = random.choice([6, 7, 8])
                dur = random.randint(300, 600)  # 5-10 min
                flow = random.uniform(1.5, 2.5)
                hot = random.uniform(40.0, 80.0)
                add_session(base_day.replace(hour=hr) + timedelta(minutes=random.randint(0, 50)), dur, flow, hot)
            # Handwashing (3-10)
            for _ in range(random.randint(3, 10)):
                hr = random.randint(7, 22)
                dur = random.randint(10, 40)
                flow = random.uniform(0.2, 0.8)
                hot = random.uniform(0.0, 40.0)
                add_session(base_day.replace(hour=hr) + timedelta(minutes=random.randint(0, 59)), dur, flow, hot)
            # Toilets (6-14), more likely in morning and evening; realistic 2.5–4.0 gpm for ~15–40s
            # Optional reflush once after 30–120s with shorter duration
            for _ in range(random.randint(6, 14)):
                # Weight morning/evening hours higher
                hours = [6, 7, 8, 9, 17, 18, 19, 20, 21, 22, 10, 11, 12, 13, 14, 15, 16, 23]
                weights = [8, 8, 8, 5, 8, 8, 8, 7, 6, 5, 3, 3, 3, 3, 3, 3, 3, 2]
                hr = random.choices(hours, weights=weights, k=1)[0]
                base_min = random.randint(0, 59)
                dur = random.randint(15, 40)
                flow = random.uniform(2.5, 4.0)
                hot = 0.0
                start_dt = base_day.replace(hour=hr) + timedelta(minutes=base_min)
                add_session(start_dt, dur, flow, hot)
                # ~30% chance of a near-term second fill (reflush/top-off)
                if random.random() < 0.30:
                    re_dur = random.randint(10, 25)
                    re_delay = random.randint(30, 120)
                    add_session(start_dt + timedelta(seconds=re_delay), re_dur, flow, hot)
            # Dishwasher (0-1)
            if random.random() < 0.6:
                hr = random.choice([20, 21, 22])
                dur = random.randint(1800, 3600)
                flow = random.uniform(0.8, 1.4)
                hot = random.uniform(30.0, 70.0)
                add_session(base_day.replace(hour=hr) + timedelta(minutes=random.randint(0, 40)), dur, flow, hot)
            # Irrigation (alternate days at ~5am)
            if include_irrigation and (dow % 2 == 0):
                hr = 5
                dur = random.randint(900, 1800)
                flow = random.uniform(1.5, 3.0)
                hot = 0.0
                add_session(base_day.replace(hour=hr) + timedelta(minutes=random.randint(-10, 10)), dur, flow, hot)

        # Bound session list size
        if len(self._state.sessions) > 5000:
            self._state.sessions = self._state.sessions[-5000:]
        await self._save()
        # Notify that data changed
        async_dispatcher_send(self.hass, engine_signal(self.entry_id), {
            "type": "ingest",
            "event": "simulation_added",
            "created": created,
        })
        return {"created": created}
