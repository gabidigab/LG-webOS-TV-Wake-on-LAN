"""Wake-on-LAN helpers for the LG webOS TV integration."""

from collections.abc import Mapping
import re
from typing import Any

from aiowebostv import WebOsTvInfo

from .const import CONF_MAC, CONF_WOL_HOST, CONF_WOL_PORT, DEFAULT_WOL_PORT

_MAC_RE = re.compile(
    r"^[0-9a-f]{2}([:-])(?:[0-9a-f]{2}\1){4}[0-9a-f]{2}$", re.IGNORECASE
)
_MAC_BARE_RE = re.compile(r"^[0-9a-f]{12}$", re.IGNORECASE)


def normalize_mac(value: str) -> str:
    """Return the MAC address as lowercase `aa:bb:cc:dd:ee:ff`.

    Raises ValueError if the format is invalid.
    """
    mac = value.strip()
    if _MAC_BARE_RE.match(mac):
        mac = ":".join(mac[i : i + 2] for i in range(0, 12, 2))
    elif not _MAC_RE.match(mac):
        raise ValueError(f"Invalid MAC address: {value}")
    return mac.replace("-", ":").lower()


def get_mac_from_tv_info(tv_info: WebOsTvInfo) -> str | None:
    """Extract the MAC address from the TV connection info, if reported.

    A connected interface is preferred, then wired, then wifi.
    """
    connection = tv_info.connection
    if not isinstance(connection, Mapping):
        return None

    candidates: list[Mapping[str, Any]] = []
    for key in ("wired", "wifi"):
        for info_key in (f"{key}Info", key):
            info = connection.get(info_key)
            if isinstance(info, Mapping):
                candidates.append(info)

    candidates.sort(key=lambda info: info.get("state") != "connected")
    for info in candidates:
        if isinstance(mac := info.get("macAddress"), str):
            try:
                return normalize_mac(mac)
            except ValueError:
                continue
    return None


def get_wol_config(
    options: Mapping[str, Any], host: str
) -> tuple[str, str, int] | None:
    """Return (mac, target host, port) if Wake-on-LAN is configured."""
    if not (mac := options.get(CONF_MAC)):
        return None
    return (
        mac,
        options.get(CONF_WOL_HOST) or host,
        options.get(CONF_WOL_PORT) or DEFAULT_WOL_PORT,
    )
