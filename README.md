# Epson ESC/VP21 over a serial-to-TCP bridge

A Home Assistant custom integration (`epson_escvp21`) for Epson projectors controlled over **ESC/VP21 on RS-232C**, reached through a transparent serial-to-TCP bridge such as a Waveshare RS232/485/422 TO POE ETH (B) in TCP Server mode.

Built and tested against an **Epson Pro Cinema 6030UB** (9600 8N1). The ESC/VP21 client lives inside the integration, so there are no PyPI requirements.

## Why not the core `epson` integration?

- **Shared replies.** The Waveshare bridge sends every projector reply to *every* connected TCP client. When another client sends queries, the core integration reads replies meant for someone else, gets out of step, and briefly reports the projector as off.
- **Wrong source command.** Over serial the core integration sends the bare source name (`HDMI2\r`) instead of `SOURCE A0\r`.

This integration handles both:

- It keeps one persistent connection and sends one command at a time.
- Before each command, it discards anything already received.
- For a query it only accepts a `KEY=` line for the key it asked about.
- A lone `ERR` counts as the answer only if no real reply follows within about 300 ms.
- A missed poll keeps the last known state. Entities only become unavailable after 3 failed polls in a row.
- If the connection drops, it reconnects with backoff (1 s up to 30 s).

The domain is distinct from core `epson`, so both can be installed. Remove the core integration if it talks to the same bridge.

## Install (HACS)

1. HACS → Custom repositories → add this repository as an *Integration*.
2. Install **Epson ESC/VP21 (serial bridge)** and restart Home Assistant.
3. Settings → Devices & services → Add integration → *Epson ESC/VP21*.
4. Enter the bridge host, port (default `3629`) and a name.

The projector can be off during setup. Setup checks `PWR?` and uses `SNO?` (the serial number) as the unique ID. If a model doesn't answer `SNO?`, setup uses `host:port` instead and switches to the serial number once the projector reports one.

**Options:** poll interval (default 10 s).

## Entities

| Entity | Notes |
|---|---|
| Media player | On/off and input only. Shows `on` while on or warming up, `off` in standby or cooling down. Power and input commands are refused while warming up or cooling down, because the projector ignores them then. |
| Select: Input | HDMI1 (`30`) / HDMI2 (`A0`) |
| Select: Color mode | `CMODE`. Names are not yet confirmed for the 6030UB, so codes show as `Code XX`. |
| Select: Aspect | `ASPECT`. Same as Color mode: shows `Code XX`. |
| Select: Lamp mode | `LUMINANCE`: 00 Normal, 01 Eco (still to be verified) |
| Sensor: Lamp hours | `LAMP`, read every 10 minutes while on |
| Sensor: Power state | standby / on / warming up / cooling down / standby (network on) / abnormal standby |
| Sensor: Error | `OK` for `ERR=00`, otherwise `Code XX` |
| Button: Switch to HDMI1 | Sends `SOURCE 30` |

The selects and the button are only available while `PWR=01`. A code with no name stays selectable once it has been seen on the projector.

To name color mode or aspect codes, add them to `CMODES` / `ASPECTS` in `custom_components/epson_escvp21/const.py`.

## Polling

- `PWR?` every cycle.
- While on: `SOURCE?`, `CMODE?`, `LUMINANCE?`, `ASPECT?` and `ERR?` every cycle, plus `LAMP?` every 10 minutes.
- While off: `PWR?` every cycle and `LAMP?` at startup and every 3 hours. `ERR?` is not sent.
- After any set command, a refresh is requested.

The 6030UB rejects `ERR?` in standby, so `ERR?` is only sent while the projector is on. The Error sensor keeps its last value while the projector is off. Lamp hours and Error both restore their values after a Home Assistant restart. If either query is rejected, it isn't treated as a fault or a lost connection: the last value is kept.

Color mode, aspect, lamp mode and input codes that the projector reports, but that have no name in `const.py`, are saved to Home Assistant's storage. They stay available as `Code XX` options after a restart, even while the projector is off.

## Action: `epson_escvp21.send_command`

Sends a raw ESC/VP21 command and returns the reply. Useful for debugging.

```yaml
action: epson_escvp21.send_command
data:
  command: "CMODE?"
response_variable: reply   # {"command": "CMODE?", "response": "CMODE=06"}
```

- A command ending in `?` is a query and returns `KEY=VALUE`.
- Any other command is a set command and returns `:`.
- A rejected command returns `ERR`.
- `config_entry_id` is only needed when more than one projector is set up.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements_test.txt
.venv/bin/pytest
```

`tests/fake_projector.py` is a fake projector plus bridge. It answers ESC/VP21 commands one at a time, like a serial line, and broadcasts every reply to every TCP client, like the Waveshare. `OtherClient` imitates another poller on the bridge. The tests check that the integration stays in step and never falsely reports off.
