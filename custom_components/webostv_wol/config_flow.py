"""Config flow for LG webOS TV integration."""

from collections.abc import Mapping
from typing import Any, Self, override
from urllib.parse import urlparse

from aiowebostv import WebOsClient, WebOsTvPairError
import probatio

from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_CLIENT_SECRET, CONF_HOST
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.ssdp import (
    ATTR_UPNP_FRIENDLY_NAME,
    ATTR_UPNP_UDN,
    SsdpServiceInfo,
)

from . import WebOsTvConfigEntry
from .const import (
    CONF_MAC,
    CONF_SOURCES,
    CONF_WOL_HOST,
    CONF_WOL_PORT,
    DEFAULT_NAME,
    DEFAULT_WOL_PORT,
    DOMAIN,
    WEBOSTV_EXCEPTIONS,
)
from .helpers import get_sources
from .wol import get_mac_from_tv_info, normalize_mac

DATA_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_HOST): cv.string,
    },
    extra=probatio.ALLOW_EXTRA,
)


WOL_SCHEMA_DICT: dict[probatio.Marker, Any] = {
    probatio.Optional(CONF_MAC): cv.string,
    probatio.Optional(CONF_WOL_HOST): cv.string,
    probatio.Optional(CONF_WOL_PORT, default=DEFAULT_WOL_PORT): cv.port,
}


def validate_wol_input(
    user_input: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, str]]:
    """Validate the Wake-on-LAN fields, return (options, errors).

    Without a MAC address Wake-on-LAN is disabled, other fields are dropped.
    """
    options: dict[str, Any] = {}
    errors: dict[str, str] = {}

    if not (mac := (user_input.get(CONF_MAC) or "").strip()):
        return options, errors

    try:
        options[CONF_MAC] = normalize_mac(mac)
    except ValueError:
        errors[CONF_MAC] = "invalid_mac"
        return {}, errors

    if wol_host := (user_input.get(CONF_WOL_HOST) or "").strip():
        options[CONF_WOL_HOST] = wol_host
    if (wol_port := user_input.get(CONF_WOL_PORT)) is not None:
        options[CONF_WOL_PORT] = wol_port

    return options, errors


async def async_control_connect(
    hass: HomeAssistant, host: str, key: str | None
) -> WebOsClient:
    """Create LG webOS client and connect to the TV."""
    client = WebOsClient(
        host,
        key,
        client_session=async_get_clientsession(hass),
    )

    await client.connect()

    return client


class FlowHandler(ConfigFlow, domain=DOMAIN):
    """LG webOS TV configuration flow."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize workflow."""
        self._host: str = ""
        self._name: str = ""
        self._uuid: str | None = None
        self._data: dict[str, Any] = {}
        self._suggested_mac: str | None = None

    @staticmethod
    @callback
    @override
    def async_get_options_flow(config_entry: WebOsTvConfigEntry) -> OptionsFlowHandler:
        """Get the options flow for this handler."""
        return OptionsFlowHandler(config_entry)

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a flow initialized by the user."""
        if user_input is not None:
            self._host = user_input[CONF_HOST]
            return await self.async_step_pairing()

        return self.async_show_form(step_id="user", data_schema=DATA_SCHEMA)

    async def async_step_pairing(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Display pairing form."""
        self._async_abort_entries_match({CONF_HOST: self._host})

        self.context["title_placeholders"] = {"name": self._name}
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                client = await async_control_connect(self.hass, self._host, None)
            except WebOsTvPairError:
                errors["base"] = "error_pairing"
            except WEBOSTV_EXCEPTIONS:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(
                    client.tv_info.hello["deviceUUID"], raise_on_progress=False
                )
                self._abort_if_unique_id_configured({CONF_HOST: self._host})
                self._data = {
                    CONF_HOST: self._host,
                    CONF_CLIENT_SECRET: client.client_key,
                }
                self._suggested_mac = get_mac_from_tv_info(client.tv_info)

                if not self._name:
                    if model_name := client.tv_info.system.get("modelName"):
                        self._name = f"{DEFAULT_NAME} {model_name}"
                    else:
                        self._name = DEFAULT_NAME
                return await self.async_step_wol()

        return self.async_show_form(step_id="pairing", errors=errors)

    async def async_step_wol(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure Wake-on-LAN, leave the MAC address empty to disable it."""
        errors: dict[str, str] = {}
        suggested: dict[str, Any] = {CONF_MAC: self._suggested_mac}

        if user_input is not None:
            options, errors = validate_wol_input(user_input)
            if not errors:
                return self.async_create_entry(
                    title=self._name, data=self._data, options=options
                )
            suggested = user_input

        return self.async_show_form(
            step_id="wol",
            data_schema=self.add_suggested_values_to_schema(
                probatio.Schema(WOL_SCHEMA_DICT), suggested
            ),
            errors=errors,
        )

    @override
    async def async_step_ssdp(
        self, discovery_info: SsdpServiceInfo
    ) -> ConfigFlowResult:
        """Handle a flow initialized by discovery."""
        assert discovery_info.ssdp_location
        host = urlparse(discovery_info.ssdp_location).hostname
        assert host
        self._host = host
        self._name = discovery_info.upnp.get(
            ATTR_UPNP_FRIENDLY_NAME, DEFAULT_NAME
        ).replace("[LG]", "LG")

        uuid = discovery_info.upnp[ATTR_UPNP_UDN]
        assert uuid
        uuid = uuid.removeprefix("uuid:")
        await self.async_set_unique_id(uuid)
        self._abort_if_unique_id_configured({CONF_HOST: self._host})

        if self.hass.config_entries.flow.async_has_matching_flow(self):
            return self.async_abort(reason="already_in_progress")

        self._uuid = uuid
        return await self.async_step_pairing()

    @override
    def is_matching(self, other_flow: Self) -> bool:
        """Return True if other_flow is matching this flow."""
        return other_flow._host == self._host

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Perform reauth upon an WebOsTvPairError."""
        self._host = entry_data[CONF_HOST]
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Dialog that informs the user that reauth is required."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                client = await async_control_connect(self.hass, self._host, None)
            except WebOsTvPairError:
                errors["base"] = "error_pairing"
            except WEBOSTV_EXCEPTIONS:
                errors["base"] = "cannot_connect"
            else:
                reauth_entry = self._get_reauth_entry()
                data = {CONF_HOST: self._host, CONF_CLIENT_SECRET: client.client_key}
                return self.async_update_reload_and_abort(reauth_entry, data=data)

        return self.async_show_form(step_id="reauth_confirm", errors=errors)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reconfiguration of the integration."""
        errors: dict[str, str] = {}
        reconfigure_entry = self._get_reconfigure_entry()

        if user_input is not None:
            host = user_input[CONF_HOST]
            client_key = reconfigure_entry.data.get(CONF_CLIENT_SECRET)

            try:
                client = await async_control_connect(self.hass, host, client_key)
            except WebOsTvPairError:
                errors["base"] = "error_pairing"
            except WEBOSTV_EXCEPTIONS:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(client.tv_info.hello["deviceUUID"])
                self._abort_if_unique_id_mismatch(reason="wrong_device")
                data = {CONF_HOST: host, CONF_CLIENT_SECRET: client.client_key}
                return self.async_update_reload_and_abort(reconfigure_entry, data=data)

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=probatio.Schema(
                {
                    probatio.Required(
                        CONF_HOST, default=reconfigure_entry.data.get(CONF_HOST)
                    ): cv.string
                }
            ),
            errors=errors,
        )


class OptionsFlowHandler(OptionsFlowWithReload):
    """Handle options."""

    def __init__(self, config_entry: WebOsTvConfigEntry) -> None:
        """Initialize options flow."""
        self.host = config_entry.data[CONF_HOST]
        self.key = config_entry.data[CONF_CLIENT_SECRET]

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        errors: dict[str, str] = {}
        option_sources = self.config_entry.options.get(CONF_SOURCES, [])
        suggested_mac = self.config_entry.options.get(CONF_MAC)

        if user_input is not None:
            wol_options, errors = validate_wol_input(user_input)
            if not errors:
                options_input = {
                    CONF_SOURCES: user_input.get(CONF_SOURCES, option_sources),
                    **wol_options,
                }
                return self.async_create_entry(title="", data=options_input)
            suggested_values = user_input
        else:
            suggested_values = {}

        # Get sources
        sources_list = []
        try:
            client = await async_control_connect(self.hass, self.host, self.key)
            sources_list = get_sources(client.tv_state)
            suggested_mac = suggested_mac or get_mac_from_tv_info(client.tv_info)
        except WebOsTvPairError:
            errors["base"] = "error_pairing"
        except WEBOSTV_EXCEPTIONS:
            errors["base"] = "cannot_connect"

        # The TV is off, offer the saved sources so they are not lost on save
        if not sources_list:
            sources_list = option_sources

        sources = [s for s in option_sources if s in sources_list]
        if not sources:
            sources = sources_list

        options_schema = probatio.Schema(
            {
                probatio.Optional(CONF_SOURCES): cv.multi_select(
                    {source: source for source in sources_list}
                ),
                **WOL_SCHEMA_DICT,
            }
        )
        suggested = {
            CONF_SOURCES: sources,
            CONF_MAC: suggested_mac,
            CONF_WOL_HOST: self.config_entry.options.get(CONF_WOL_HOST),
            CONF_WOL_PORT: self.config_entry.options.get(
                CONF_WOL_PORT, DEFAULT_WOL_PORT
            ),
            **suggested_values,
        }

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(options_schema, suggested),
            errors=errors,
        )
