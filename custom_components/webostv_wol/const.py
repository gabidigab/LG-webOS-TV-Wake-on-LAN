"""Constants for the LG webOS TV integration."""

import asyncio
import logging

import aiohttp
from aiowebostv import WebOsTvCommandError

from homeassistant.const import Platform

DOMAIN = "webostv_wol"
LOGGER = logging.getLogger(__package__)
PLATFORMS = [Platform.MEDIA_PLAYER, Platform.SWITCH]
DEFAULT_NAME = "LG webOS TV"

ATTR_PAYLOAD = "payload"
ATTR_SOUND_OUTPUT = "sound_output"

CONF_ON_ACTION = "turn_on_action"
CONF_SOURCES = "sources"
CONF_MAC = "mac"
CONF_WOL_HOST = "wol_host"
CONF_WOL_PORT = "wol_port"

DEFAULT_WOL_PORT = 9
WOL_PACKET_COUNT = 3
WOL_PACKET_INTERVAL = 1.0
FAST_POLL_DURATION = 30.0
FAST_POLL_INTERVAL = 2.0

LIVE_TV_APP_ID = "com.webos.app.livetv"

WEBOSTV_EXCEPTIONS = (
    ConnectionResetError,
    WebOsTvCommandError,
    aiohttp.ClientConnectorError,
    aiohttp.ServerDisconnectedError,
    aiohttp.WSMessageTypeError,
    asyncio.CancelledError,
    asyncio.TimeoutError,
)
