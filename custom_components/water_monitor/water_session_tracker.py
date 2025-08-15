"""Water session tracking logic - with accurate hot water accumulation and derived metrics."""
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from dataclasses import dataclass

_LOGGER = logging.getLogger(__name__)

@dataclass
class WaterSession:
    """Represents a completed water session."""
    start_time: datetime
    end_time: datetime
    duration: int  # seconds
    volume: float  # gallons
    hot_water_duration: int  # seconds
    gapped_sessions: int
    average_flow: float  # gallons per minute

    @property
    def hot_water_percentage(self) -> float:
        """Calculate hot water percentage."""
        if self.duration == 0:
            return 0.0
        return round((self.hot_water_duration / self.duration) * 100, 1)

class WaterSessionTracker:
    """Tracks water usage sessions with gap tolerance and hot water monitoring."""
    
    def __init__(
        self,
        min_session_volume: float = 0.0,
        min_session_duration: int = 0,
        session_gap_tolerance: int = 5,
        session_continuity_window: int = 3,
    ):
        self.min_session_volume = min_session_volume
        self.min_session_duration = min_session_duration
        self.session_gap_tolerance = session_gap_tolerance
        self.session_continuity_window = session_continuity_window
        
        # Current state
        self.flow_rate = 0.0
        self.volume_total = 0.0
        self.hot_water_active = False
        
        # Previous state for change detection/accumulation
        self._prev_flow_rate = 0.0
        self._prev_hot_water_active = False
        self._prev_session_active = False
        self._prev_gap_active = False
        
        # Session tracking
        self._session_active = False
        self._gap_active = False
        self._original_session_start: Optional[datetime] = None
        self._current_session_start: Optional[datetime] = None
        self._session_start_volume = 0.0
        self._gapped_sessions_count = 0

        # Time accumulation
        self._last_update_ts: Optional[datetime] = None
        self._current_session_duration_secs: int = 0
        self._current_hot_water_duration_secs: int = 0

        # Intermediate session (for gap handling)
        self._intermediate_exists = False
        self._intermediate_start: Optional[datetime] = None
        self._intermediate_duration = 0
        self._intermediate_volume = 0.0
        self._intermediate_hot_water_duration = 0
        
        # Session continuation tracking
        self._session_end_candidate_time: Optional[datetime] = None
        
        # Last completed session
        self.last_session: Optional[WaterSession] = None

    def _ensure_utc(self, dt: datetime) -> datetime:
        """Ensure datetime is in UTC timezone."""
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    def _accumulate_time(self, timestamp: datetime) -> None:
        """Accumulate session and hot water durations based on previous flags."""
        if self._last_update_ts is None:
            self._last_update_ts = timestamp
            return

        delta = int((timestamp - self._last_update_ts).total_seconds())
        if delta < 0:
            delta = 0

        # Accumulate using previous flags (what was true during the elapsed interval)
        if self._prev_session_active:
            self._current_session_duration_secs += delta
            if self._prev_hot_water_active:
                self._current_hot_water_duration_secs += delta

        self._last_update_ts = timestamp

    def update(
        self, 
        flow_rate: float, 
        volume_total: float, 
        hot_water_active: bool = False,
        timestamp: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """
        Update the tracker with new sensor values.
        Returns current session state and debug info.
        """
        if timestamp is None:
            timestamp = datetime.now(timezone.utc)
        else:
            timestamp = self._ensure_utc(timestamp)
            
        # Store previous flags for accumulation/transition decisions
        prev_session_active = self._prev_session_active
        prev_gap_active = self._prev_gap_active

        # Accumulate time using previous flags
        self._accumulate_time(timestamp)
            
        # Update current values
        self.flow_rate = max(0.0, float(flow_rate))
        self.volume_total = max(0.0, float(volume_total))
        self.hot_water_active = bool(hot_water_active)

        # Start session if flow > 0 and not currently active
        if self.flow_rate > 0 and not self._session_active:
            self._session_active = True
            self._gap_active = False
            self._original_session_start = timestamp
            self._current_session_start = timestamp
            self._session_start_volume = self.volume_total
            self._intermediate_exists = False
            self._intermediate_start = None
            self._intermediate_duration = 0
            self._intermediate_volume = 0.0
            self._intermediate_hot_water_duration = 0
            self._gapped_sessions_count = 0
            self._session_end_candidate_time = None
            # Reset accumulators
            self._current_session_duration_secs = 0
            self._current_hot_water_duration_secs = 0

        # If session active and flow is zero, we may be in a gap
        if self._session_active and self.flow_rate == 0:
            if not self._gap_active:
                # Entering gap: snapshot intermediate values
                self._gap_active = True
                if self._current_session_start is not None:
                    elapsed = self._current_session_duration_secs
                    volume = max(0.0, self.volume_total - self._session_start_volume)
                    self._intermediate_exists = True
                    self._intermediate_start = self._current_session_start
                    self._intermediate_duration = elapsed
                    self._intermediate_volume = volume
                    self._intermediate_hot_water_duration = self._current_hot_water_duration_secs
                # Mark potential end start
                self._session_end_candidate_time = timestamp
            else:
                # Already in gap: check gap tolerance to confirm candidate end
                if (
                    self._session_end_candidate_time is not None
                    and (timestamp - self._session_end_candidate_time).total_seconds() >= self.session_gap_tolerance
                ):
                    # gap tolerance exceeded; nothing else to do here — continuation window handled below
                    pass

        # If flow resumes during a gap
        if self._session_active and self.flow_rate > 0 and self._gap_active:
            self._gap_active = False
            self._gapped_sessions_count += 1
            # Reset the end candidate because session continues
            self._session_end_candidate_time = None

        # If no flow and we have a candidate, check continuation window to finalize
        if (
            self._session_active
            and self.flow_rate == 0
            and self._session_end_candidate_time is not None
        ):
            if (timestamp - self._session_end_candidate_time).total_seconds() >= self.session_continuity_window:
                # finalize session
                start = self._current_session_start or timestamp
                end = timestamp
                duration = int((end - start).total_seconds())
                volume = max(0.0, self.volume_total - self._session_start_volume)
                hot_dur = self._current_hot_water_duration_secs
                avg_flow = (volume / duration * 60.0) if duration > 0 else 0.0

                session = WaterSession(
                    start_time=start,
                    end_time=end,
                    duration=duration,
                    volume=volume,
                    hot_water_duration=hot_dur,
                    gapped_sessions=self._gapped_sessions_count,
                    average_flow=avg_flow,
                )

                # Apply filters
                if volume >= self.min_session_volume and duration >= self.min_session_duration:
                    self.last_session = session

                # Reset session state
                self._session_active = False
                self._gap_active = False
                self._original_session_start = None
                self._current_session_start = None
                self._session_start_volume = 0.0
                self._gapped_sessions_count = 0
                self._intermediate_exists = False
                self._intermediate_start = None
                self._intermediate_duration = 0
                self._intermediate_volume = 0.0
                self._intermediate_hot_water_duration = 0
                self._session_end_candidate_time = None
                # Keep accumulators as-is; they will be reset on next session start
                self._current_session_duration_secs = 0
                self._current_hot_water_duration_secs = 0

        # Build derived metrics
        current_session_volume = max(0.0, self.volume_total - self._session_start_volume) if self._session_active else 0.0
        current_session_duration = self._current_session_duration_secs if self._session_active else 0
        current_session_avg_flow = (current_session_volume / current_session_duration * 60.0) if current_session_duration > 0 else 0.0
        current_session_hot_pct = (
            round((self._current_hot_water_duration_secs / current_session_duration) * 100, 1)
            if current_session_duration > 0 else 0.0
        )

        intermediate_avg_flow = (
            (self._intermediate_volume / self._intermediate_duration * 60.0)
            if self._intermediate_exists and self._intermediate_duration > 0 else 0.0
        )
        intermediate_hot_pct = (
            round((self._intermediate_hot_water_duration / self._intermediate_duration) * 100, 1)
            if self._intermediate_exists and self._intermediate_duration > 0 else 0.0
        )

        # Build state data
        state: Dict[str, Any] = {
            "current_session_active": self._session_active,
            "gap_active": self._gap_active,
            "current_session_start": self._current_session_start.isoformat() if self._current_session_start else None,
            "original_session_start": self._original_session_start.isoformat() if self._original_session_start else None,
            "current_session_volume": current_session_volume,
            "current_session_duration": current_session_duration,
            "current_session_average_flow": current_session_avg_flow,
            "current_session_hot_water_pct": current_session_hot_pct,
            "intermediate_session_exists": self._intermediate_exists,
            "intermediate_session_start": self._intermediate_start.isoformat() if self._intermediate_start else None,
            "intermediate_session_duration": self._intermediate_duration,
            "intermediate_session_volume": self._intermediate_volume,
            "intermediate_session_average_flow": intermediate_avg_flow,
            "intermediate_session_hot_water_duration": self._intermediate_hot_water_duration,
            "intermediate_session_hot_water_pct": intermediate_hot_pct,
            # Last completed session summary (includes timestamps)
            "last_session_start": self.last_session.start_time.isoformat() if self.last_session else None,
            "last_session_end": self.last_session.end_time.isoformat() if self.last_session else None,
            "last_session_volume": self.last_session.volume if self.last_session else 0.0,
            "last_session_duration": self.last_session.duration if self.last_session else 0,
            "last_session_hot_water_pct": self.last_session.hot_water_percentage if self.last_session else 0.0,
            "last_session_gapped_sessions": self.last_session.gapped_sessions if self.last_session else 0,
            "last_session_average_flow": self.last_session.average_flow if self.last_session else 0.0,
            "flow_sensor_value": self.flow_rate,
            "debug_state": "ACTIVE" if self._session_active else ("GAP" if self._gap_active else "IDLE"),
        }

        # Cache previous flags for next update
        self._prev_flow_rate = self.flow_rate
        self._prev_session_active = self._session_active
        self._prev_gap_active = self._gap_active
        self._prev_hot_water_active = self.hot_water_active

        return state