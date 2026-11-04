# AWOL Aetherion Projector for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![Validate](https://github.com/skynet01/ha-awol-aetherion/actions/workflows/validate.yml/badge.svg)](https://github.com/skynet01/ha-awol-aetherion/actions/workflows/validate.yml)

Local network control for **AWOL Vision Aetherion** projectors (Max, Pro, Plus), using
AWOL's official IP control protocol (PJLink with AWOL extensions, TCP/UDP port 4352).
No cloud and no ADB.

## Features

- **Media player**: power, input (Google TV, HDMI 1-3, DP, AV), volume step and mute.
  The volume slider only appears when the built-in speaker is in use.
- **Remote**: navigation keys (up, down, left, right, ok, back, home, menu, settings),
  usable from remote cards and `remote.send_command`.
- **Picture settings**: picture mode (follows SDR, HDR, HDR10+ and Dolby Vision),
  motion enhancement, gamma, dynamic tone mapping, color temperature, enhanced black
  level, and a manual focus button.
- **Sensors**: input resolution, signal timing, HDR and audio format, picture size,
  laser level, temperatures, fan speeds, and a problem alert.
- **Power on from standby** with your own switch or Wake-on-LAN (see below).
- **`aetherion.send_command` action**: send any raw protocol command and get the reply.
- LAN discovery, instant status updates, and gentle polling while the projector is off.

## Requirements

- Aetherion firmware **20260929** or newer.
- On the projector, enable **Network control**.
- To use Wake-on-LAN, also enable **network optimized standby**.

## Installation

**HACS (recommended)**

1. In HACS, open the three-dot menu, choose **Custom repositories**, and add
   `https://github.com/skynet01/ha-awol-aetherion` with type **Integration**.
2. Install **AWOL Aetherion Projector**, then restart Home Assistant.

**Manual**: copy `custom_components/aetherion` into your `config/custom_components/`
folder, then restart Home Assistant.

## Setup

1. Turn the projector **on**.
2. Go to **Settings > Devices & services > Add integration** and choose
   **AWOL Aetherion Projector**.
3. Pick the projector found on your network, or enter its IP address. A password is
   only needed if you set one for network control on the projector.

Give the projector's IP address(es) a DHCP reservation in your router.

## Power control

In standby the projector closes its control port, so the regular power-on command
can't reach it. Choose how Home Assistant powers it on under the integration's
**Configure** (options). Without either option below, power it on with the remote.

**Your own power entity**

Pick a switch, smart plug, relay, or an IR blaster script as the **Power-on entity**.
It's turned on when the projector's control port is closed. The projector then shows
as on while it boots, and the integration sends power-on as soon as it answers.

Tick **Also turn this entity off after the projector is off** to cut power again. It's
built not to cut power early:

1. The projector must accept Home Assistant's off command.
2. It must be confirmed off: its control port closed, or it still reports standby
   after 5 minutes.
3. The cool-down must pass (default 120 seconds, adjustable).

If the projector never confirms it's off, comes back on during the cool-down, or you
turn it on again, the entity stays on. If the projector was turned off some other way,
such as with the remote, or isn't reachable, the entity is left alone.

An entity can only power the projector on if turning it on makes the projector start.
If the entity is already on, turning it on does nothing.

**Wake-on-LAN** (off by default)

Needs **network optimized standby** on the projector. Tick **Use Wake-on-LAN** and
enter:

| Option | Value |
| --- | --- |
| Wi-Fi MAC address | MAC of the projector's Wi-Fi interface (Ethernet's MAC does not wake it) |
| Wi-Fi IP address | Only needed if you set the projector up on its Ethernet IP |

The packet brings Wi-Fi up in about 2 seconds, the integration sends power-on, and
the projector is fully on in about 10 seconds.

**Known limitation:** it isn't always reliable. In testing it worked after 8.5 hours
in standby once, and stopped answering after about 2 hours another time, with the
projector's Wi-Fi still connected. The cause isn't known yet. If it doesn't answer,
`turn_on` shows an error instead of failing silently.

If both are set, the power entity goes first and Wake-on-LAN is sent as well.

## Notes

- Laser level is read-only, because the projector refuses changes while dynamic iris
  is on.
- Less common sensors (fans, color details, extra temperature slots) start hidden.
  Temperature and fan slots your model doesn't have hide themselves automatically.
- Settings that don't apply to the current signal show as unavailable, for example
  tone mapping in SDR.

## Development

```bash
uv venv -p 3.14 .venv && uv pip install -p .venv pytest-homeassistant-custom-component
.venv/bin/python -m pytest
```

`tests/fake_projector.py` emulates the projector's TCP protocol, including
authentication.

Not affiliated with AWOL Vision.
