"""Simulate realistic household water usage with parallel event threads.

Requires env vars:
- HA_BASE_URL (e.g., http://10.0.0.55:8123)
- HA_TOKEN (long-lived token)

Usage (PowerShell examples):
  pwsh -NoProfile -c "python .\scripts\simulate_random_usage.py"

Configuration (edit defaults below or pass via env):
- FLOW_ENTITY: entity to adjust (e.g., input_number.water_flow_gpm)
- EVENTS: faucets, showers, washer, toilet, dishwasher, irrigation
- Each event contributes a delta to flow when active, and removes it when done.
- Durations and inter-arrival times are randomized per typical usage patterns.
"""
from __future__ import annotations

import os
import random
import threading
import time
from dataclasses import dataclass

try:
    from sim_utils import adjust_flow
except ImportError:
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.append(str(_Path(__file__).parent))
    from sim_utils import adjust_flow

FLOW_ENTITY = os.environ.get("FLOW_ENTITY", "input_number.water_flow_gpm")
RNG_SEED = int(os.environ.get("SIM_RNG_SEED", "0") or 0)
random.seed(RNG_SEED)

# Global stop flag
STOP = False

@dataclass
class EventSpec:
    name: str
    delta: float  # gpm contribution
    min_dur: float
    max_dur: float
    min_idle: float
    max_idle: float
    burst_prob: float = 0.0  # chance to repeat back-to-back
    duration_jitter: float = 0.2  # +/- percent


def jittered_duration(base: float, jitter: float) -> float:
    j = 1.0 + random.uniform(-jitter, jitter)
    return max(0.1, base * j)


def run_event_loop(spec: EventSpec):
    """Loop: wait random idle, start event, hold for duration, then stop, respecting STOP flag."""
    while not STOP:
        idle = random.uniform(spec.min_idle, spec.max_idle)
        time.sleep(idle)
        if STOP:
            break
        # Start event (increase flow)
        new_val = adjust_flow(FLOW_ENTITY, spec.delta)
        if new_val is None:
            continue  # skip this iteration if failed
        # Hold for randomized duration
        base = random.uniform(spec.min_dur, spec.max_dur)
        dur = jittered_duration(base, spec.duration_jitter)
        t0 = time.time()
        while not STOP and (time.time() - t0) < dur:
            time.sleep(0.5)
        # End event (decrease flow by same delta)
        adjust_flow(FLOW_ENTITY, -spec.delta)
        # Chance to burst (back-to-back repeats)
        if spec.burst_prob > 0 and random.random() < spec.burst_prob:
            # short pause then repeat without full idle
            time.sleep(random.uniform(0.2, 1.0))
            continue


def start_threads(specs: list[EventSpec]) -> list[threading.Thread]:
    threads = []
    for s in specs:
        th = threading.Thread(target=run_event_loop, args=(s,), name=f"sim-{s.name}", daemon=True)
        th.start()
        threads.append(th)
    return threads


def main():
    global STOP
    specs = [
        # Typical contributions and cadence
        EventSpec("faucet", delta=0.6, min_dur=5, max_dur=60, min_idle=10, max_idle=120, burst_prob=0.1),
        EventSpec("shower", delta=2.2, min_dur=180, max_dur=900, min_idle=600, max_idle=3600, burst_prob=0.0),
        EventSpec("washer", delta=2.5, min_dur=120, max_dur=600, min_idle=1800, max_idle=7200, burst_prob=0.0, duration_jitter=0.3),
        EventSpec("toilet", delta=1.6, min_dur=5, max_dur=12, min_idle=60, max_idle=600, burst_prob=0.2),
        EventSpec("dishwasher", delta=1.2, min_dur=60, max_dur=300, min_idle=1800, max_idle=7200, burst_prob=0.0, duration_jitter=0.3),
        EventSpec("irrigation", delta=3.5, min_dur=600, max_dur=2400, min_idle=14400, max_idle=86400, burst_prob=0.0, duration_jitter=0.4),
    ]
    threads = start_threads(specs)
    print("Simulation started. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Stopping simulation...")
        STOP = True
        for th in threads:
            th.join(timeout=2)
        print("Stopped.")


if __name__ == "__main__":
    main()
