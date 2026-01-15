from __future__ import annotations

import asyncio
import logging

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import YourApiClient
from .const import DOMAIN, CONF_BASE_URL, CONF_API_KEY

_LOGGER = logging.getLogger(__name__)


class YourApiConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors: dict[str, str] = {}

        if user_input is not None:
            base_url = user_input[CONF_BASE_URL].strip().rstrip("/")
            api_key = user_input[CONF_API_KEY].strip()

            try:
                session = async_get_clientsession(self.hass)
                client = YourApiClient(session, base_url, api_key)

                # Validation call (make sure api.py points at a real/light endpoint)
                data = await client.fetch_readings()
                _LOGGER.debug("Validation OK. Returned keys: %s", list((data or {}).keys()))

            except aiohttp.ClientResponseError as e:
                # e.status is the key bit (401/403/404/500 etc)
                _LOGGER.warning(
                    "API HTTP error during validation: status=%s url=%s message=%s",
                    e.status,
                    getattr(getattr(e, "request_info", None), "real_url", None),
                    getattr(e, "message", ""),
                )
                errors["base"] = f"http_{e.status}"

            except aiohttp.ClientConnectorCertificateError as e:
                _LOGGER.warning("SSL certificate error during validation: %s", e)
                errors["base"] = "ssl_error"

            except aiohttp.ClientConnectorError as e:
                _LOGGER.warning("Connection error during validation: %s", e)
                errors["base"] = "cannot_connect"

            except asyncio.TimeoutError:
                _LOGGER.warning("Timeout during validation")
                errors["base"] = "timeout"

            except Exception as e:
                _LOGGER.exception("Unexpected error during validation: %s", e)
                errors["base"] = "unknown"

            else:
                # Optional: prevent duplicates by base_url
                await self.async_set_unique_id(base_url.lower())
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title="plaatobbc", data=user_input)

        schema = vol.Schema(
            {
                vol.Required(CONF_BASE_URL): str,
                vol.Required(CONF_API_KEY): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)
