# Intelligent Leak Detection: Design Overview

This document captures the architecture and algorithms for the intelligent, model-driven leak detection feature. It serves as a reference for tuning and future iterations.

## Goals
- Real-time leak detection based on the current session stream.
- Hour-of-day and context-aware thresholds learned over a long horizon (≈60 days).
- Robust to normal variability (e.g., irrigation), including in Away/Vacation modes.
- Transparent, debuggable, and adjustable over time.

## Key Concepts
- Current-session based detection: triggers even if sessions never end.
- Offline learning (engine): computes per-bucket baselines and recurring prototypes.
- Context features: raw occupancy state, occupancy class, people count bins, day type.
- Fallback ladders: gracefully broaden when bucket is sparse.

## Data Captured Per Session
- ended_at (local time), hour (0–23), day_type (weekday/weekend)
- duration_s, avg_flow, volume, hot_pct
- occupancy_raw (string from the user’s occupancy entity at session start)
- occupancy_class (derived: home/away/vacation/night using configured Away/Vacation lists and heuristics)
- people_bin (0, 1, 2–3, 4+) from person.* entities in state=home at session start

## Learning (Daily, 60-Day Rolling Window)
- Maintain rolling stats with exponential decay (recent days weigh more):
  - For each bucket key: (hour, day_type, occupancy_raw, people_bin)
    - count, p50, p90, p95, p99 for duration_s and avg_flow
  - Baseline readiness when count ≥ 10 (configurable later)
- Recurring prototypes (for irrigation and similar):
  - Group by (day_of_week, hour-window) and cluster on [duration_s, avg_flow, hot_pct]
  - A prototype is normal if:
    - Seen ≥ 3 times across the horizon
    - Start times within ±15–20 minutes
    - Duration and avg_flow within ±20% tolerance
    - Typically low hot_pct (often cold irrigation)
  - Confidence decays with time since last_seen; prototypes expire if unseen recently

## Fallback Ladder (When Sparse)
1. (hour, day_type, occupancy_raw, people_bin)
2. (hour, day_type, occupancy_class, people_bin)
3. (hour, day_type, occupancy_class)
4. (hour, day_type)
5. (hour)
6. global

## Real-Time Detection (binary_sensor.intelligent_leak)
- Subscribes to live tracker state (current_session_*), flow_rate, gap_active, hot water info.
- Also reads occupancy_raw/class and people_bin snapshots (live) and hour/day_type.
- Decision flow:
  1. Try to match an ongoing stream to a high-confidence prototype (time alignment + value tolerances). If matched, treat as normal even in Away/Vacation.
  2. Else, fetch the best-available bucket stats and compute a risk score:
     - Risk increases when duration exceeds learned p95/p99 and when avg_flow exceeds typical.
     - Apply night factor when occupancy_class is night (default 0.8 on duration threshold).
     - People_bin=0 adds risk; higher people reduce risk slightly.
  3. Trigger ON when risk ≥ 1.0 (or a confirm gate is passed), independent of session end.

### Occupancy Policy
- Vacation: strict; only prototypes suppress alerts.
- Away: moderate; prototypes suppress alerts; otherwise bucket thresholds apply.
- Home-like (Wake Up, Home, Wind Down, Night): bucket thresholds apply; night factor tightens threshold.

## Defaults (Tunable Later)
- Horizon: 60 days; min samples for readiness: 10
- Prototype min occurrences: 3; time window: ±15–20 min; tolerance: ±20%; decay half-life: 30 days
- Percentile for max normal duration: p95 (consider p99 to reduce alerts)
- Night factor: 0.8 on duration threshold when occupancy_class is night

## Integration Touchpoints
- Engine storage: sessions, daily summaries, hourly_stats, prototypes with metadata
- Dispatcher topics:
  - tracker_state_<entry_id> (live tracker updates)
  - engine_updated_<entry_id> (ingest/daily summaries)
- Entities:
  - binary_sensor.intelligent_leak (real-time)
  - Optional: binary_sensor.engine_status (anomaly display), diagnostic sensors

## Telemetry & Debugging
- Attributes on intelligent_leak:
  - bucket_used, baseline_ready, count, p95/p99 values
  - matched_prototype_id, prototype_confidence, start_time_delta_min
  - risk_score, reasons[]
  - occupancy_raw/class, people_bin, elapsed_s, flow_now, hot_pct_current

## Future Enhancements
- Make horizon/bucket thresholds configurable via options flow
- Add canonical location_mode SelectEntity and a service for user sync
- Include irrigation controller signals (if available) to label prototypes
- Persist partial-session prototypes for very long runs
- More granular day-type (weekday number), holiday handling, seasonality
