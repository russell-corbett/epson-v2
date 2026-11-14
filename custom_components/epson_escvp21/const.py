"""Constants for the Epson ESC/VP21 integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "epson_escvp21"
MANUFACTURER: Final = "Epson"

DEFAULT_PORT: Final = 3629
DEFAULT_NAME: Final = "Epson projector"

CONF_SCAN_INTERVAL: Final = "scan_interval"
DEFAULT_SCAN_INTERVAL: Final = 10  # seconds
MIN_SCAN_INTERVAL: Final = 5
MAX_SCAN_INTERVAL: Final = 300

# Lamp hours change slowly; poll them far less often than the rest.
LAMP_POLL_INTERVAL: Final = timedelta(minutes=10)
# In standby LAMP? still answers; read it at startup and this often.
LAMP_POLL_INTERVAL_OFF: Final = timedelta(hours=3)
# While the projector is not on, ERR? is tried at startup and this often.
# (The 6030UB answers ERR in standby; the last known value is then kept.)
ERR_POLL_INTERVAL_OFF: Final = timedelta(minutes=5)

# Consecutive failed polls before entities are marked unavailable.
MAX_CONSECUTIVE_FAILURES: Final = 3

SERVICE_SEND_COMMAND: Final = "send_command"
ATTR_COMMAND: Final = "command"
ATTR_TIMEOUT: Final = "timeout"

# --- ESC/VP21 keys --------------------------------------------------------

KEY_POWER: Final = "PWR"
KEY_SOURCE: Final = "SOURCE"
KEY_CMODE: Final = "CMODE"
KEY_LUMINANCE: Final = "LUMINANCE"
KEY_ASPECT: Final = "ASPECT"
KEY_ERROR: Final = "ERR"
KEY_LAMP: Final = "LAMP"
KEY_SERIAL: Final = "SNO"

# --- Power ----------------------------------------------------------------

PWR_STANDBY: Final = "00"
PWR_ON: Final = "01"
PWR_WARMING: Final = "02"
PWR_COOLING: Final = "03"
PWR_STANDBY_NETWORK: Final = "04"
PWR_ABNORMAL: Final = "05"

# Sensor enum option per PWR code. Translated in strings.json.
POWER_STATES: Final[dict[str, str]] = {
    PWR_STANDBY: "standby",
    PWR_ON: "on",
    PWR_WARMING: "warming_up",
    PWR_COOLING: "cooling_down",
    PWR_STANDBY_NETWORK: "standby_network",
    PWR_ABNORMAL: "abnormal_standby",
}
POWER_STATE_UNKNOWN: Final = "unknown_code"

# Set commands are ignored by the projector in these states.
PWR_BUSY: Final = frozenset({PWR_WARMING, PWR_COOLING})

# --- Value maps -----------------------------------------------------------
# Only codes confirmed for the Pro Cinema 6030UB belong here. Codes that are
# not listed are shown as "Code XX" (see code_to_option) and can still be
# selected once they have been observed.

SOURCES: Final[dict[str, str]] = {
    "30": "HDMI1",
    "A0": "HDMI2",
}

# Static code -> name maps. These options exist from startup, before the
# projector has ever been on. Codes the projector reports that are not listed
# here are learned, persisted across restarts and shown as "Code XX".
#
# CMODE: awaiting the owner's confirmed list for the Pro Cinema 6030UB.
# Live value seen: 06.
CMODES: Final[dict[str, str]] = {}

# ASPECT: not yet confirmed for this model. Live value seen: 00.
ASPECTS: Final[dict[str, str]] = {}

# Supplied by the owner, still to be verified against Epson's command list.
LUMINANCE_MODES: Final[dict[str, str]] = {
    "00": "Normal",
    "01": "Eco",
}

CODE_PREFIX: Final = "Code "


def code_to_option(code: str, names: dict[str, str]) -> str:
    """Return the display option for a projector code."""
    return names.get(code, f"{CODE_PREFIX}{code}")


def option_to_code(option: str, names: dict[str, str]) -> str | None:
    """Return the projector code for a display option, or None if unknown."""
    for code, name in names.items():
        if name == option:
            return code
    if option.startswith(CODE_PREFIX):
        code = option.removeprefix(CODE_PREFIX).strip().upper()
        if code and code.isalnum():
            return code
    return None
