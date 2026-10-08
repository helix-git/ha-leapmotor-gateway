"""Setup through Supervisor discovery (like Music Assistant) or by hand.

Nothing secret is asked here. Accounts, passwords and PINs live in the app only. The integration
knows the address and token of the app.
"""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.hassio import HassioServiceInfo

from .api import GatewayClient, GatewayError
from .const import CONF_HOST, CONF_PORT, CONF_TOKEN, DEFAULT_PORT, DOMAIN

TITLE = "Leapmotor Gateway"


class GatewayConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._discovered: dict[str, Any] = {}

    async def _check(self, data: dict) -> str | None:
        client = GatewayClient(async_get_clientsession(self.hass), data[CONF_HOST], data[CONF_PORT], data[CONF_TOKEN])
        try:
            await client.vehicles()
        except GatewayError:
            return "cannot_connect"
        return None

    async def async_step_hassio(self, discovery_info: HassioServiceInfo) -> ConfigFlowResult:
        c = discovery_info.config
        self._discovered = {CONF_HOST: c["host"], CONF_PORT: int(c.get("port", DEFAULT_PORT)), CONF_TOKEN: c["token"]}
        # Already set up (there is only one gateway): take over address and token.
        for entry in self._async_current_entries(include_ignore=False):
            self.hass.config_entries.async_update_entry(entry, data=self._discovered)
            self.hass.config_entries.async_schedule_reload(entry.entry_id)
            return self.async_abort(reason="already_configured")
        if await self._check(self._discovered):
            return self.async_abort(reason="cannot_connect")
        await self.async_set_unique_id(DOMAIN)
        return await self.async_step_hassio_confirm()

    async def async_step_hassio_confirm(self, user_input: dict | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title=TITLE, data=self._discovered)
        return self.async_show_form(step_id="hassio_confirm")

    async def async_step_user(self, user_input: dict | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if err := await self._check(user_input):
                errors["base"] = err
            else:
                await self.async_set_unique_id(DOMAIN)
                return self.async_create_entry(title=TITLE, data=user_input)
        schema = vol.Schema({vol.Required(CONF_HOST, default="local-leapmotor-gateway"): str,
                             vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
                             vol.Required(CONF_TOKEN): str})
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)
