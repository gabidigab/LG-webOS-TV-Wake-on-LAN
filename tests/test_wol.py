"""Test the Wake-on-LAN support of the LG webOS TV integration."""

from collections.abc import Generator
from datetime import timedelta
from unittest.mock import MagicMock, patch

from aiowebostv import WebOsTvInfo
from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.webostv_wol import const
from custom_components.webostv_wol.const import (
    CONF_MAC,
    CONF_WOL_HOST,
    CONF_WOL_PORT,
    DOMAIN,
)
from custom_components.webostv_wol.wol import (
    get_mac_from_tv_info,
    get_wol_config,
    normalize_mac,
)
from homeassistant.components import automation
from homeassistant.components.media_player import (
    DOMAIN as MP_DOMAIN,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_SUPPORTED_FEATURES,
    SERVICE_TURN_ON,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.setup import async_setup_component

from . import setup_webostv
from .const import ENTITY_ID, HOST, MAC

WOL_PATH = "custom_components.webostv_wol.coordinator.wakeonlan.send_magic_packet"


@pytest.fixture(name="send_magic_packet")
def send_magic_packet_fixture() -> Generator[MagicMock]:
    """Patch the wakeonlan library."""
    with patch(WOL_PATH) as mock_send:
        yield mock_send


@pytest.fixture(name="fast_timings")
def fast_timings_fixture() -> Generator[None]:
    """Shrink the packet spacing and the fast polling window."""
    with patch.multiple(
        "custom_components.webostv_wol.coordinator",
        WOL_PACKET_INTERVAL=0.01,
        FAST_POLL_INTERVAL=0.01,
        FAST_POLL_DURATION=0.5,
    ):
        yield


async def mock_scan_interval(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Mock update interval to force an update."""
    freezer.tick(timedelta(seconds=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


def power_off_tv(client) -> None:
    """Make the TV unreachable."""
    client.tv_state.is_on = False
    client.is_connected.return_value = False
    client.connect.side_effect = TimeoutError


async def turn_on(hass: HomeAssistant) -> None:
    """Call media_player.turn_on."""
    await hass.services.async_call(
        MP_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: ENTITY_ID}, blocking=True
    )


def test_timings() -> None:
    """Test the documented timings: 3 packets, 1 s apart, ~30 s of fast polling."""
    assert const.WOL_PACKET_COUNT == 3
    assert const.WOL_PACKET_INTERVAL == 1.0
    assert const.FAST_POLL_DURATION == 30.0
    assert const.FAST_POLL_INTERVAL < const.WOL_PACKET_INTERVAL * 5


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("00:1a:2b:3c:4d:5e", MAC),
        ("00:1A:2B:3C:4D:5E", MAC),
        ("00-1a-2b-3c-4d-5e", MAC),
        ("001a2b3c4d5e", MAC),
        (" 00:1a:2b:3c:4d:5e ", MAC),
    ],
)
def test_normalize_mac(value: str, expected: str) -> None:
    """Test MAC address normalization."""
    assert normalize_mac(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "zz",
        "00:1a:2b:3c:4d",
        "00:1a:2b:3c:4d:5e:ff",
        "00:1a-2b:3c:4d:5e",
        "gg:1a:2b:3c:4d:5e",
    ],
)
def test_normalize_mac_invalid(value: str) -> None:
    """Test invalid MAC addresses are rejected."""
    with pytest.raises(ValueError, match="Invalid MAC address"):
        normalize_mac(value)


@pytest.mark.parametrize(
    ("connection", "expected"),
    [
        ({}, None),
        ({"wired": {"state": "connected"}}, None),
        ({"wired": {"state": "connected", "macAddress": "bad"}}, None),
        ({"wired": {"state": "connected", "macAddress": "00:1A:2B:3C:4D:5E"}}, MAC),
        ({"wiredInfo": {"macAddress": "00:1A:2B:3C:4D:5E"}}, MAC),
        (
            {
                "wired": {"state": "disconnected", "macAddress": "AA:AA:AA:AA:AA:AA"},
                "wifi": {"state": "connected", "macAddress": "00:1a:2b:3c:4d:5e"},
            },
            MAC,
        ),
    ],
)
def test_get_mac_from_tv_info(connection: dict, expected: str | None) -> None:
    """Test the MAC address is read from the TV connection info."""
    assert get_mac_from_tv_info(WebOsTvInfo(connection=connection)) == expected


def test_get_wol_config() -> None:
    """Test the Wake-on-LAN config defaults."""
    assert get_wol_config({}, HOST) is None
    assert get_wol_config({CONF_MAC: MAC}, HOST) == (MAC, HOST, 9)
    assert get_wol_config(
        {CONF_MAC: MAC, CONF_WOL_HOST: "192.168.1.50", CONF_WOL_PORT: 7}, HOST
    ) == (MAC, "192.168.1.50", 7)


async def test_off_instead_of_unavailable_with_mac(
    hass: HomeAssistant, client, freezer: FrozenDateTimeFactory
) -> None:
    """Test the entity is off, not unavailable, when the TV doesn't answer."""
    await setup_webostv(hass, options={CONF_MAC: MAC})
    assert hass.states.get(ENTITY_ID).state == MediaPlayerState.ON

    power_off_tv(client)
    await mock_scan_interval(hass, freezer)

    state = hass.states.get(ENTITY_ID)
    assert state.state == MediaPlayerState.OFF
    assert state.attributes[ATTR_SUPPORTED_FEATURES] & MediaPlayerEntityFeature.TURN_ON


async def test_unavailable_without_mac(
    hass: HomeAssistant, client, freezer: FrozenDateTimeFactory
) -> None:
    """Test the original behavior is kept without MAC address."""
    await setup_webostv(hass)
    assert hass.states.get(ENTITY_ID).state == MediaPlayerState.ON

    power_off_tv(client)
    await mock_scan_interval(hass, freezer)

    state = hass.states.get(ENTITY_ID)
    assert state.state == STATE_UNAVAILABLE


async def test_supported_features_turn_on(hass: HomeAssistant, client) -> None:
    """Test TURN_ON is only supported when a MAC address is configured."""
    await setup_webostv(hass, options={CONF_MAC: MAC})
    features = hass.states.get(ENTITY_ID).attributes[ATTR_SUPPORTED_FEATURES]
    assert features & MediaPlayerEntityFeature.TURN_ON


async def test_supported_features_no_turn_on_without_mac(
    hass: HomeAssistant, client
) -> None:
    """Test TURN_ON is not supported without MAC address nor automation."""
    await setup_webostv(hass)
    features = hass.states.get(ENTITY_ID).attributes[ATTR_SUPPORTED_FEATURES]
    assert not features & MediaPlayerEntityFeature.TURN_ON


@pytest.mark.usefixtures("fast_timings")
async def test_turn_on_sends_magic_packets_to_tv_ip(
    hass: HomeAssistant, client, send_magic_packet: MagicMock
) -> None:
    """Test turn_on sends 3 unicast magic packets to the TV IP, UDP port 9."""
    await setup_webostv(hass, options={CONF_MAC: MAC})
    power_off_tv(client)

    await turn_on(hass)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert send_magic_packet.call_count == 3
    for call in send_magic_packet.call_args_list:
        assert call.args == (MAC,)
        assert call.kwargs == {"ip_address": HOST, "port": 9}


@pytest.mark.usefixtures("fast_timings")
async def test_turn_on_custom_host_and_port(
    hass: HomeAssistant, client, send_magic_packet: MagicMock
) -> None:
    """Test the configured target host and port are used."""
    await setup_webostv(
        hass,
        options={CONF_MAC: MAC, CONF_WOL_HOST: "192.168.1.50", CONF_WOL_PORT: 7},
    )
    power_off_tv(client)

    await turn_on(hass)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert send_magic_packet.call_count == 3
    send_magic_packet.assert_called_with(MAC, ip_address="192.168.1.50", port=7)


@pytest.mark.usefixtures("fast_timings")
async def test_turn_on_reconnects_quickly(
    hass: HomeAssistant, client, send_magic_packet: MagicMock
) -> None:
    """Test the connection is retried every few seconds until the TV answers."""
    await setup_webostv(hass, options={CONF_MAC: MAC})
    power_off_tv(client)
    client.connect.reset_mock()

    attempts = 0

    async def connect() -> bool:
        nonlocal attempts
        attempts += 1
        if attempts < 4:
            raise TimeoutError
        client.tv_state.is_on = True
        client.is_connected.return_value = True
        return True

    client.connect.side_effect = connect

    await turn_on(hass)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert attempts == 4
    assert send_magic_packet.call_count == 3
    assert hass.states.get(ENTITY_ID).state == MediaPlayerState.ON


@pytest.mark.usefixtures("fast_timings")
async def test_turn_on_fast_polling_stops(
    hass: HomeAssistant, client, send_magic_packet: MagicMock
) -> None:
    """Test fast polling ends when the TV never answers."""
    await setup_webostv(hass, options={CONF_MAC: MAC})
    power_off_tv(client)

    await turn_on(hass)
    await hass.async_block_till_done(wait_background_tasks=True)

    attempts = client.connect.call_count
    assert attempts > 3
    await hass.async_block_till_done(wait_background_tasks=True)
    assert client.connect.call_count == attempts
    assert hass.states.get(ENTITY_ID).state == MediaPlayerState.OFF


@pytest.mark.usefixtures("fast_timings")
async def test_turn_on_send_error(
    hass: HomeAssistant, client, send_magic_packet: MagicMock
) -> None:
    """Test a send failure is reported to the caller."""
    await setup_webostv(hass, options={CONF_MAC: MAC})
    power_off_tv(client)
    client.connect.reset_mock()
    send_magic_packet.side_effect = OSError("Network is unreachable")

    with pytest.raises(HomeAssistantError, match="Network is unreachable"):
        await turn_on(hass)
    await hass.async_block_till_done(wait_background_tasks=True)

    send_magic_packet.assert_called_once()
    client.connect.assert_not_called()


@pytest.mark.usefixtures("fast_timings")
async def test_turn_on_runs_automation_too(
    hass: HomeAssistant, client, send_magic_packet: MagicMock
) -> None:
    """Test the webostv_wol.turn_on trigger still runs next to Wake-on-LAN."""
    await setup_webostv(hass, options={CONF_MAC: MAC})
    calls: list[ServiceCall] = []
    hass.services.async_register("test", "automation", calls.append)

    assert await async_setup_component(
        hass,
        automation.DOMAIN,
        {
            automation.DOMAIN: {
                "trigger": {
                    "platform": f"{DOMAIN}.turn_on",
                    "entity_id": ENTITY_ID,
                },
                "action": {"service": "test.automation"},
            }
        },
    )
    power_off_tv(client)

    await turn_on(hass)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert len(calls) == 1
    assert send_magic_packet.call_count == 3


async def test_turn_on_automation_only(
    hass: HomeAssistant, client, send_magic_packet: MagicMock
) -> None:
    """Test the trigger alone still works, without any magic packet."""
    await setup_webostv(hass)
    calls: list[ServiceCall] = []
    hass.services.async_register("test", "automation", calls.append)

    assert await async_setup_component(
        hass,
        automation.DOMAIN,
        {
            automation.DOMAIN: {
                "trigger": {
                    "platform": f"{DOMAIN}.turn_on",
                    "entity_id": ENTITY_ID,
                },
                "action": {"service": "test.automation"},
            }
        },
    )
    power_off_tv(client)

    await turn_on(hass)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert len(calls) == 1
    send_magic_packet.assert_not_called()


async def test_reconnects_after_power_cycle(
    hass: HomeAssistant, client, freezer: FrozenDateTimeFactory
) -> None:
    """Test the client is reconnected by the normal polling after a power cycle."""
    await setup_webostv(hass, options={CONF_MAC: MAC})

    # TV switched off: connection lost
    power_off_tv(client)
    await client.mock_state_update()
    await mock_scan_interval(hass, freezer)
    assert hass.states.get(ENTITY_ID).state == MediaPlayerState.OFF

    # TV switched on again: next poll creates a new connection
    client.connect.reset_mock()

    async def connect() -> bool:
        client.tv_state.is_on = True
        client.is_connected.return_value = True
        return True

    client.connect.side_effect = connect
    await mock_scan_interval(hass, freezer)

    client.connect.assert_called_once()
    assert hass.states.get(ENTITY_ID).state == MediaPlayerState.ON
