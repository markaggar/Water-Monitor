"""Simulate a dedicated tank leak as a steady low flow with occasional variability.

- Increases a flow entity by `LEAK_DELTA` at start and holds it until stopped.
- Optionally adds small random jitter around the baseline to resemble real leaks.

Env:
- HA_BASE_URL, HA_TOKEN
- FLOW_ENTITY (default: input_number.water_flow_gpm)
- LEAK_DELTA (default: 0.2 gpm)
- JITTER (default: 0.05 gpm)
- JITTER_INTERVAL (default: 15s)
"""
from __future__ import annotations

import os
import random
import signal
import sys
import time

try:
    from sim_utils import adjust_flow
except ImportError:
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.append(str(_Path(__file__).parent))
    from sim_utils import adjust_flow

FLOW_ENTITY = os.environ.get("FLOW_ENTITY", "input_number.water_flow_gpm")
LEAK_DELTA = float(os.environ.get("LEAK_DELTA", "0.2"))
JITTER = float(os.environ.get("JITTER", "0.05"))
JITTER_INTERVAL = float(os.environ.get("JITTER_INTERVAL", "15"))

RUNNING = True


def handle_sigint(signum, frame):
    global RUNNING
    RUNNING = False


def main():
    global RUNNING
    signal.signal(signal.SIGINT, handle_sigint)
    signal.signal(signal.SIGTERM, handle_sigint)

    print(f"Starting tank leak simulation: +{LEAK_DELTA} gpm on {FLOW_ENTITY}")
    base = adjust_flow(FLOW_ENTITY, LEAK_DELTA)
    if base is None:
        print("Failed to set initial leak delta; exiting.")
        sys.exit(1)

    last_jitter = 0.0
    try:
        while RUNNING:
            # Random small jitter +/- JITTER around baseline every interval
            delta = random.uniform(-JITTER, JITTER)
            # Compensate previous jitter then apply new
            adj = -last_jitter + delta
            res = adjust_flow(FLOW_ENTITY, adj)
            if res is not None:
                last_jitter = delta
            time.sleep(JITTER_INTERVAL)
    finally:
        # Remove leak delta and any residual jitter
        print("Stopping tank leak; removing delta...")
        adjust_flow(FLOW_ENTITY, -(LEAK_DELTA + last_jitter))
        print("Leak simulation stopped.")


if __name__ == "__main__":
    main()
