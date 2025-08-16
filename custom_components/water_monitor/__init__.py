from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN, CONF_SENSOR_PREFIX
from .engine import WaterMonitorEngine  # new engine

# Platforms provided by this integration
PLATFORMS: list[str] = ["sensor", "binary_sensor", "number"]


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
    domain_data[entry.entry_id] = {"engine": engine, "synthetic_flow_gpm": 0.0}
    await engine.start()

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