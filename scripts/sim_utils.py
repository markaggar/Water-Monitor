"""Utilities to interact with Home Assistant for simulation scripts.

Config:
- HA_BASE_URL: Base URL, e.g. http://10.0.0.55:8123
- HA_TOKEN: Long-lived token

Helpers:
- get_state(entity_id) -> float | None
- set_flow_value(entity_id, value) -> bool
- adjust_flow(entity_id, delta) -> float | None
- set_binary(entity_id, on: bool) -> bool (supports input_boolean)
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
import urllib.error
from typing import Optional


BASE_URL = os.environ.get("HA_BASE_URL", "").rstrip("/")
TOKEN = os.environ.get("HA_TOKEN", "")


def _headers() -> dict:
    if not TOKEN:
        raise RuntimeError("HA_TOKEN env var is required")
    return {
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json",
    }


def _url(path: str) -> str:
    if not BASE_URL:
        raise RuntimeError("HA_BASE_URL env var is required")
    return f"{BASE_URL}{path}"


def _request(method: str, path: str, payload: Optional[dict] = None, timeout: int = 15):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(_url(path), data=data, method=method, headers=_headers())
    return urllib.request.urlopen(req, timeout=timeout)


def get_state(entity_id: str) -> Optional[float]:
    try:
        with _request("GET", f"/api/states/{entity_id}") as resp:
            if resp.status != 200:
                return None
            obj = json.loads(resp.read().decode("utf-8"))
            st = obj.get("state")
            try:
                return float(st)
            except Exception:
                return None
    except urllib.error.HTTPError as e:
        # 404 if entity not found
        return None
    except Exception:
        return None


def set_flow_value(entity_id: str, value: float) -> bool:
    """Set a flow value either via input_number service or states API."""
    value = float(value)
    value = round(max(0.0, value), 3)
    try:
        if entity_id.startswith("input_number."):
            payload = {"entity_id": entity_id, "value": value}
            with _request("POST", "/api/services/input_number/set_value", payload) as resp:
                return 200 <= resp.status < 300
        # states API fallback for sensor-like entities
        payload = {"state": str(value)}
        with _request("POST", f"/api/states/{entity_id}", payload) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


def adjust_flow(entity_id: str, delta: float, retries: int = 3, backoff: float = 0.4) -> Optional[float]:
    """Adjust flow by delta, reading current state and writing new value.
    Returns the new value on success, None on failure.
    """
    last_err = None
    for i in range(retries):
        cur = get_state(entity_id)
        if cur is None:
            last_err = "no_current"
            time.sleep(backoff)
            continue
        new_val = round(max(0.0, cur + float(delta)), 3)
        if set_flow_value(entity_id, new_val):
            return new_val
        last_err = "set_failed"
        time.sleep(backoff * (i + 1))
    return None


def set_binary(entity_id: str, on: bool) -> bool:
    """Toggle an input_boolean or write to a states API for testing-only binary sensors."""
    try:
        if entity_id.startswith("input_boolean."):
            service = "/api/services/input_boolean/turn_on" if on else "/api/services/input_boolean/turn_off"
            payload = {"entity_id": entity_id}
            with _request("POST", service, payload) as resp:
                return 200 <= resp.status < 300
        payload = {"state": "on" if on else "off"}
        with _request("POST", f"/api/states/{entity_id}", payload) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False
