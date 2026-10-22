"""Coordinator for the LG webOS TV integration."""

import asyncio
from datetime import timedelta
from functools import partial
from typing import override

import aiohttp
from aiowebostv import WebOsClient, WebOsTvPairError, WebOsTvState
import wakeonlan

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_CLIENT_SECRET, CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.trigger import PluggableAction
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DOMAIN,
    FAST_POLL_DURATION,
    FAST_POLL_INTERVAL,
    LOGGER,
    WEBOSTV_EXCEPTIONS,
    WOL_PACKET_COUNT,
    WOL_PACKET_INTERVAL,
)
from .wol import get_wol_config

SCAN_INTERVAL = timedelta(seconds=10)

# client.connect() can raise any error that happens while connecting
# (TV booting, network unreachable, handshake rejected), none of them
# should make the entity unavailable when the TV is known to be wakeable.
CONNECT_EXCEPTIONS = (*WEBOSTV_EXCEPTIONS, aiohttp.ClientError, OSError)

type WebOsTvConfigEntry = ConfigEntry[WebOsTvDataUpdateCoordinator]


class WebOsTvDataUpdateCoordinator(DataUpdateCoordinator[None]):
    """Coordinator for the LG webOS TV integration."""

    config_entry: WebOsTvConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: WebOsTvConfigEntry,
        client: WebOsClient,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            LOGGER,
            config_entry=config_entry,
            name=config_entry.title,
            update_interval=SCAN_INTERVAL,
        )

        self.client = client
        self.turn_on = PluggableAction(self.async_update_listeners)
        self._wake_task: asyncio.Task[None] | None = None

    @property
    def wol_config(self) -> tuple[str, str, int] | None:
        """Return (mac, host, port) if Wake-on-LAN is configured."""
        return get_wol_config(
            self.config_entry.options, self.config_entry.data[CONF_HOST]
        )

    @property
    def can_turn_on(self) -> bool:
        """Return True if the TV can be turned on by Wake-on-LAN or an action."""
        return bool(self.turn_on) or self.wol_config is not None

    @override
    async def _async_update_data(self) -> None:
        """Connect to LG webOS TV if not connected."""
        if self.client.is_connected():
            return

        host = self.config_entry.data[CONF_HOST]
        LOGGER.debug("Connecting to LG webOS TV %s", host)
        try:
            await self.client.connect()
        except CONNECT_EXCEPTIONS as error:
            LOGGER.debug("Connection to LG webOS TV %s failed: %r", host, error)
            if (
                not self.can_turn_on
                and self.config_entry.state is ConfigEntryState.LOADED
            ):
                # can't recover if the TV is disconnected and no way to turn it on
                raise UpdateFailed(
                    translation_domain=DOMAIN,
                    translation_key="device_unavailable",
                    translation_placeholders={"device": self.name},
                ) from error
        except WebOsTvPairError as error:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="auth_failed",
                translation_placeholders={"device": self.name},
            ) from error
        else:
            LOGGER.debug("Connected to LG webOS TV %s", host)
            update_client_key(self.hass, self.config_entry, self.client)

    async def async_handle_update(self, tv_state: WebOsTvState) -> None:
        """Handle state update from TV."""
        LOGGER.debug(
            "State update from LG webOS TV %s: is_on=%s",
            self.config_entry.data[CONF_HOST],
            tv_state.is_on,
        )
        if self.last_update_success:
            # client.connect() trigger an update on failure,
            # avoid marking the device as available
            self.async_set_updated_data(None)

    async def async_wake(self) -> None:
        """Send a Wake-on-LAN magic packet and poll quickly until the TV answers.

        The first packet is sent right away so that errors are reported to the
        caller, the remaining packets and the fast polling run in the background.
        """
        if self.wol_config is None:
            return

        await self._async_send_magic_packet(1)

        if self._wake_task is not None:
            self._wake_task.cancel()
        self._wake_task = self.config_entry.async_create_background_task(
            self.hass,
            self._async_resend_and_poll(),
            name=f"{DOMAIN} wake {self.config_entry.title}",
        )

    async def _async_send_magic_packet(self, number: int) -> None:
        """Send one magic packet without blocking the event loop."""
        if (wol_config := self.wol_config) is None:
            return

        mac, host, port = wol_config
        LOGGER.debug(
            "Sending Wake-on-LAN packet %d/%d for %s to %s:%s",
            number,
            WOL_PACKET_COUNT,
            mac,
            host,
            port,
        )
        try:
            await self.hass.async_add_executor_job(
                partial(wakeonlan.send_magic_packet, mac, ip_address=host, port=port)
            )
        except (OSError, ValueError) as error:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="wol_send_failed",
                translation_placeholders={
                    "name": self.name,
                    "host": host,
                    "error": str(error),
                },
            ) from error

    async def _async_resend_and_poll(self) -> None:
        """Send the remaining packets then reconnect quickly once the TV boots."""
        loop = self.hass.loop
        deadline = loop.time() + FAST_POLL_DURATION

        for number in range(2, WOL_PACKET_COUNT + 1):
            await asyncio.sleep(WOL_PACKET_INTERVAL)
            try:
                await self._async_send_magic_packet(number)
            except HomeAssistantError as error:
                LOGGER.warning("%s", error)

        LOGGER.debug(
            "Polling LG webOS TV %s every %ss for up to %ss",
            self.config_entry.data[CONF_HOST],
            FAST_POLL_INTERVAL,
            FAST_POLL_DURATION,
        )
        while (
            loop.time() < deadline
            and self.config_entry.state is ConfigEntryState.LOADED
        ):
            await self.async_refresh()
            if self.client.is_connected():
                LOGGER.debug("LG webOS TV %s answered", self.name)
                return
            await asyncio.sleep(FAST_POLL_INTERVAL)

        LOGGER.debug("LG webOS TV %s did not answer, back to normal polling", self.name)


def update_client_key(
    hass: HomeAssistant, entry: WebOsTvConfigEntry, client: WebOsClient
) -> None:
    """Check and update stored client key if key has changed."""
    if client.client_key != entry.data[CONF_CLIENT_SECRET]:
        host = entry.data[CONF_HOST]
        LOGGER.debug("Updating client key for host %s", host)
        data = {CONF_HOST: host, CONF_CLIENT_SECRET: client.client_key}
        hass.config_entries.async_update_entry(entry, data=data)
