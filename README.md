# LG webOS TV (Wake-on-LAN)

Home Assistant custom integration derived from the official
[LG webOS TV](https://www.home-assistant.io/integrations/webostv) integration,
with **Wake-on-LAN built in**: no external automation using the
`webostv.turn_on` trigger is needed anymore.

- Domain: `webostv_wol` (coexists with the official `webostv` integration).
- Based on `homeassistant/components/webostv` at tag **2026.10.2**.
- With a MAC address configured:
  - `media_player.turn_on` sends the magic packet (3 packets 1 s apart, unicast
    to the TV IP by default), then retries the connection every 2 s for ~30 s so
    the state switches to `on` as soon as the TV answers;
  - the entity is `off` (not `unavailable`) when the TV is switched off, and
    `TURN_ON` is part of its supported features.
- Without a MAC address, the original behavior is kept.
- The `webostv_wol.turn_on` trigger (and device trigger) still exists: if an
  automation is attached to it, it runs **in addition to** the built-in
  Wake-on-LAN.

## Installation

### Manual

1. Copy the `custom_components/webostv_wol` folder into the
   `config/custom_components/` folder of Home Assistant.
2. Restart Home Assistant.
3. *Settings → Devices & services → Add integration →*
   **LG webOS TV (Wake-on-LAN)**.

### HACS (custom repository)

1. HACS → ⋮ menu → *Custom repositories* → paste
   `https://github.com/gabidigab/LG-webOS-TV-Wake-on-LAN`, category **Integration**.
2. Install, restart, then add the integration as above.

Targeted Home Assistant version: **2026.10** (see `hacs.json`).

## Configuration

Adding the integration works like the official one: TV IP address (TV switched
on), then pairing (accept the request on the TV). A **Wake-on-LAN** step follows:

| Field      | Required                 | Default     | Purpose                                                                                     |
| ---------- | ------------------------ | ----------- | ------------------------------------------------------------------------------------------- |
| `mac`      | Yes, to enable WOL       | —           | TV MAC address (`AA:BB:CC:DD:EE:FF`; `-` separators or none are accepted). Empty = WOL off. |
| `wol_host` | No                       | TV IP       | Host the magic packet is sent to, in unicast.                                               |
| `wol_port` | No                       | `9`         | Destination UDP port.                                                                       |

The MAC address is prefilled when the TV reports it (webOS network info). These
fields can be changed at any time: *Devices & services → LG webOS TV
(Wake-on-LAN) → Configure*. The options flow works while the TV is off (the
sources already selected are kept).

The MAC address is shown on the TV: *Settings → All settings → General →
System → About this TV* (Wi-Fi and wired MACs differ: use the one of the
interface actually in use).

### Debug logs

```yaml
logger:
  logs:
    custom_components.webostv_wol: debug
    aiowebostv: debug
```

Every connection attempt, every WOL packet sent (`n/3`, MAC, host, port) and the
end of fast polling are logged.

## Troubleshooting

### The magic packet does not arrive (separate VLANs)

A directed broadcast does not cross VLANs: leave `wol_host` empty (or set the TV
IP) to send in **unicast**. The router must then be able to deliver the packet
to a switched-off TV:

- **Static ARP entry**: a sleeping TV no longer answers ARP requests, the
  router entry expires and the unicast packet is lost. Statically bind the TV IP
  to its MAC on the router/firewall (in the TV's VLAN). Check with
  `arp -an | grep <TV IP>`.
- **Firewall rule**: allow Home Assistant → TV on **UDP 9** (or your
  `wol_port`), in addition to TCP 3000-3001 (webOS websocket) and ICMP.
- Check the packet reaches the firewall interface:
  `tcpdump -ni <TV interface> udp port 9`, then press *Turn on*.
- Test from Home Assistant (terminal):
  `python3 -c "import wakeonlan; wakeonlan.send_magic_packet('00:1a:2b:3c:4d:5e', ip_address='192.168.1.50')"`

### The TV receives the packet but does not turn on

Enable the setting that keeps the TV reachable in standby (names vary by model):

- *Settings → All settings → General → Devices → External devices →
  TV management →* **Turn on via Wi-Fi** (or via wired network);
- *Settings → General → System → Quick Start+ / **Always Ready***: keeps the TV
  in active standby and makes wake-up reliable;
- on some models, disable the energy saving option that cuts the network in
  standby.

For an Ethernet cable use the wired MAC; for Wi-Fi use the Wi-Fi MAC.

### The entity stays `unavailable`

- Without a configured MAC this is the official integration's behavior (unless
  an automation is attached to `webostv_wol.turn_on`): enter the MAC in
  *Configure*.
- With a MAC, the entity only becomes `unavailable` if pairing is rejected
  (re-authentication requested); otherwise it stays `off`.

### The entity stays `off` after *Turn on*

Fast polling lasts ~30 s. If the TV boots more slowly, the reconnection happens
on the next normal cycle (10 s). Check in the debug logs that the 3 packets are
sent and that TCP 3000/3001 is allowed.

## Development

```bash
pip install -r requirements_test.txt
pytest
```

The tests are those of the official integration (adapted to the `webostv_wol`
domain) plus Wake-on-LAN coverage (`tests/test_wol.py`,
`tests/test_config_flow.py`).
