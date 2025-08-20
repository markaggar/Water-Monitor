from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import config_validation as cv
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN, CONF_SENSOR_PREFIX, CONF_WATER_SHUTOFF_ENTITY, tracker_signal
import logging
_LOGGER = logging.getLogger(__name__)
from .engine import WaterMonitorEngine  # new engine

# Platforms provided by this integration
PLATFORMS: list[str] = ["sensor", "binary_sensor", "number"]

# This integration is config-entry only (no YAML).
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the integration at Home Assistant start."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if not domain_data.get("services_registered"):
        async def _handle_analyze(call: ServiceCall) -> None:
            target_id: str | None = call.data.get("entry_id")
            results = {}
            targets = []
            if target_id:
                data = hass.data.get(DOMAIN, {}).get(target_id)
                if data and data.get("engine"):
                    targets.append((target_id, data["engine"]))
            else:
                for eid, data in hass.data.get(DOMAIN, {}).items():
                    if isinstance(data, dict) and data.get("engine"):
                        targets.append((eid, data["engine"]))
            for eid, eng in targets:
                try:
                    summary = await eng.analyze_yesterday()
                    results[eid] = summary
                except Exception as e:  # pragma: no cover - defensive
                    results[eid] = {"error": str(e)}
            if not results:
                return

        hass.services.async_register(DOMAIN, "analyze_yesterday", _handle_analyze)
        
        async def _handle_simulate(call: ServiceCall) -> None:
            target_id: str | None = call.data.get("entry_id")
            days: int = int(call.data.get("days", 14) or 14)
            seed = call.data.get("seed")
            include_irrigation = bool(call.data.get("include_irrigation", True))
            targets = []
            if target_id:
                data = hass.data.get(DOMAIN, {}).get(target_id)
                if data and data.get("engine"):
                    targets.append(data["engine"])
            else:
                for eid, data in hass.data.get(DOMAIN, {}).items():
                    if isinstance(data, dict) and data.get("engine"):
                        targets.append(data["engine"])
            for eng in targets:
                try:
                    await eng.simulate_history(days=days, seed=seed, include_irrigation=include_irrigation)
                except Exception:
                    pass

        hass.services.async_register(DOMAIN, "simulate_history", _handle_simulate)
        domain_data["services_registered"] = True
    return True


async def _update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    # Reload entry so options changes take effect and entities are created/removed accordingly
    await hass.config_entries.async_reload(entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # Ensure device registry has the desired manufacturer/model and name
    ex = {**entry.data, **entry.options}
    prefix = ex.get(CONF_SENSOR_PREFIX) or entry.title or "Water Monitor"

    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
    )

    # Keep a user-customized name if set; otherwise use the prefix
    desired_name = device.name if device.name_by_user else prefix
    desired_manufacturer = "markaggar"
    desired_model = "Water Session Tracking and Leak Detection"

    if (
        device.name != desired_name
        or device.manufacturer != desired_manufacturer
        or device.model != desired_model
    ):
        dev_reg.async_update_device(
            device_id=device.id,
            name=desired_name,
            manufacturer=desired_manufacturer,
            model=desired_model,
        )

    # Create and start engine
    domain_data = hass.data.setdefault(DOMAIN, {})
    engine = WaterMonitorEngine(hass, entry.entry_id, ex)
    domain_data[entry.entry_id] = {
        "engine": engine,
        "synthetic_flow_gpm": 0.0,
        "valve_entity_id": ex.get(CONF_WATER_SHUTOFF_ENTITY) or "",
        "valve_off": False,
        "_unsub_valve": None,
    }
    await engine.start()

    # Track optional water shutoff valve state and react to changes
    async def _eval_valve_state(entity_id: str | None) -> None:
        try:
            data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
            if not isinstance(data, dict):
                _LOGGER.warning("[valve] No domain data for entry %s", entry.entry_id)
                return
            valve = data.get("valve_entity_id")
            if not valve:
                data["valve_off"] = False
                _LOGGER.info("[valve] No valve entity configured for entry %s", entry.entry_id)
                return
            st = hass.states.get(valve)
            off = False
            if st and st.state not in (None, "unknown", "unavailable"):
                dom = valve.split(".")[0]
                sval = str(st.state).lower()
                _LOGGER.info("[valve] Entity %s state: %s (domain: %s)", valve, sval, dom)
                if dom == "valve":
                    off = sval == "closed"
                elif dom in ("switch", "input_boolean"):
                    off = sval == "off"
            else:
                _LOGGER.info("[valve] Entity %s is unavailable or unknown", valve)
            data["valve_off"] = bool(off)
            _LOGGER.info("[valve] Set valve_off=%s for entry %s", bool(off), entry.entry_id)
            # Always fire tracker signal so all leak sensors re-evaluate immediately
            try:
                hass.helpers.dispatcher.async_dispatcher_send(tracker_signal(entry.entry_id), {})
            except Exception as e:
                _LOGGER.error("[valve] Error firing tracker signal: %s", e)
            if off:
                # Force synthetic flow number to 0 if present (by unique_id and by explicit entity_id)
                try:
                    ent_reg = er.async_get(hass)
                    target_uid = f"{entry.entry_id}_synthetic_flow_gpm"
                    ent = next((e for e in ent_reg.entities.values() if e.platform == DOMAIN and e.unique_id == target_uid), None)
                    if ent is not None:
                        await hass.services.async_call(
                            "number", "set_value", {"entity_id": ent.entity_id, "value": 0}, blocking=False
                        )
                except Exception:
                    pass
                # Always set the explicit entity_id for synth flow if present, using the integration's prefix
                try:
                    prefix = (entry.options.get("sensor_prefix") or entry.data.get("sensor_prefix") or entry.title or "water_monitor").lower().replace(" ", "_")
                    entity_id = f"number.{prefix}_synth_synthetic_flow_gpm"
                    await hass.services.async_call(
                        "number", "set_value", {"entity_id": entity_id, "value": 0}, blocking=False
                    )
                except Exception:
                    pass
        except Exception:
            pass

    # Subscribe to valve state changes if configured
    dd = domain_data.get(entry.entry_id)
    valve_ent = dd.get("valve_entity_id") if isinstance(dd, dict) else None
    if valve_ent:
        async def _on_valve_event(event):
            await _eval_valve_state(valve_ent)
        try:
            dd["_unsub_valve"] = async_track_state_change_event(hass, [valve_ent], _on_valve_event)
        except Exception:
            dd["_unsub_valve"] = None
        # Evaluate once at startup
        await _eval_valve_state(valve_ent)

    # Register a one-time service to trigger daily analysis on demand
    # Useful for testing without waiting until the scheduled time.
    if not domain_data.get("services_registered"):
        async def _handle_analyze(call: ServiceCall) -> None:
            target_id: str | None = call.data.get("entry_id")
            results = {}
            # Run for a specific entry or for all entries
            targets = []
            if target_id:
                data = hass.data.get(DOMAIN, {}).get(target_id)
                if data and data.get("engine"):
                    targets.append((target_id, data["engine"]))
            else:
                for eid, data in hass.data.get(DOMAIN, {}).items():
                    if isinstance(data, dict) and data.get("engine"):
                        targets.append((eid, data["engine"]))
            for eid, eng in targets:
                try:
                    summary = await eng.analyze_yesterday()
                    results[eid] = summary
                except Exception as e:  # pragma: no cover - defensive
                    results[eid] = {"error": str(e)}
            # Best-effort logging of results
            # Avoid importing logger here; engine already logs a summary.
            if not results:
                return

        hass.services.async_register(DOMAIN, "analyze_yesterday", _handle_analyze)
        
        async def _handle_simulate(call: ServiceCall) -> None:
            target_id: str | None = call.data.get("entry_id")
            days: int = int(call.data.get("days", 14) or 14)
            seed = call.data.get("seed")
            include_irrigation = bool(call.data.get("include_irrigation", True))
            targets = []
            if target_id:
                data = hass.data.get(DOMAIN, {}).get(target_id)
                if data and data.get("engine"):
                    targets.append(data["engine"])
            else:
                for eid, data in hass.data.get(DOMAIN, {}).items():
                    if isinstance(data, dict) and data.get("engine"):
                        targets.append(data["engine"])
            for eng in targets:
                try:
                    await eng.simulate_history(days=days, seed=seed, include_irrigation=include_irrigation)
                except Exception:
                    pass

        hass.services.async_register(DOMAIN, "simulate_history", _handle_simulate)
        domain_data["services_registered"] = True

    # Reload on options changes
    entry.async_on_unload(entry.add_update_listener(_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # Stop engine and unload platforms
    domain_data = hass.data.get(DOMAIN, {})
    data = domain_data.pop(entry.entry_id, None)
    if data and data.get("engine"):
        await data["engine"].stop()
    # Unsubscribe valve listener
    try:
        unsub = data.get("_unsub_valve") if isinstance(data, dict) else None
        if unsub:
            unsub()
    except Exception:
        pass
    # If this was the last engine, remove the on-demand service
    any_engines_left = any(
        isinstance(v, dict) and v.get("engine") is not None for v in domain_data.values()
    )
    if not any_engines_left and domain_data.get("services_registered"):
        try:
            hass.services.async_remove(DOMAIN, "analyze_yesterday")
            hass.services.async_remove(DOMAIN, "simulate_history")
        except Exception:  # pragma: no cover - defensive
            pass
        domain_data.pop("services_registered", None)

    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)