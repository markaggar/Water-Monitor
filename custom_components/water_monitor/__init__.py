from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN, CONF_SENSOR_PREFIX
from .engine import WaterMonitorEngine  # new engine

# Platforms provided by this integration
PLATFORMS: list[str] = ["sensor", "binary_sensor", "number"]


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
    hass.data.setdefault(DOMAIN, {})
    engine = WaterMonitorEngine(hass, entry.entry_id, ex)
    hass.data[DOMAIN][entry.entry_id] = {"engine": engine}
    await engine.start()

    # Reload on options changes
    entry.async_on_unload(entry.add_update_listener(_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # Stop engine and unload platforms
    data = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if data and data.get("engine"):
        await data["engine"].stop()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)