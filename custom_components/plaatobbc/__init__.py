from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import YourApiClient
from .const import (
    DOMAIN, PLATFORMS,
    CONF_BASE_URL, CONF_API_KEY, CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    session = async_get_clientsession(hass)

    client = YourApiClient(
        session=session,
        base_url=entry.data[CONF_BASE_URL],
        api_key=entry.data[CONF_API_KEY],
    )

    scan_seconds = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)

    async def _async_update():
        try:
            return await client.fetch_readings()
        except Exception as err:
            raise UpdateFailed(f"API error: {err}") from err

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=f"{DOMAIN}_{entry.entry_id}",
        update_method=_async_update,
        update_interval=timedelta(seconds=scan_seconds),
    )

    await coordinator.async_config_entry_first_refresh()
    _LOGGER.warning("PlaatoBBC coordinator first refresh complete")


    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "client": client,
        "coordinator": coordinator,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
