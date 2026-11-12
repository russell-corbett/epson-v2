"""Integration-level tests: coordinator, entities and the send_command action."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.epson_escvp21.const import DOMAIN, MAX_CONSECUTIVE_FAILURES

from .fake_projector import FakeProjector, OtherClient

MP = "media_player.epson_projector"


async def refresh(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()


async def test_setup_and_states(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    entry = setup_entry
    assert entry.state is ConfigEntryState.LOADED
    # host:port unique_id is replaced by the serial number once on.
    assert entry.unique_id == "TZ2F380077L"

    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, entry.entry_id), entry.entry_id
    )
    assert device.serial_number == "TZ2F380077L"

    mp = hass.states.get(MP)
    assert mp.state == STATE_ON
    assert mp.attributes["source"] == "HDMI1"
    assert mp.attributes["source_list"] == ["HDMI1", "HDMI2"]
    assert "volume_level" not in mp.attributes

    assert hass.states.get("sensor.epson_projector_lamp_hours").state == "1275"
    assert hass.states.get("sensor.epson_projector_power_state").state == "on"
    assert hass.states.get("sensor.epson_projector_error").state == "OK"
    assert hass.states.get("select.epson_projector_input").state == "HDMI1"
    # Unconfirmed codes are shown, not named.
    assert hass.states.get("select.epson_projector_color_mode").state == "Code 06"
    assert hass.states.get("select.epson_projector_aspect").state == "Code 00"
    assert hass.states.get("select.epson_projector_lamp_mode").state == "Normal"
    assert hass.states.get("button.epson_projector_switch_to_hdmi1").state != STATE_UNAVAILABLE


async def test_off_state(
    hass: HomeAssistant, setup_entry: MockConfigEntry, projector: FakeProjector
) -> None:
    projector.state["PWR"] = "00"
    await refresh(hass, setup_entry)
    assert hass.states.get(MP).state == STATE_OFF
    assert hass.states.get("sensor.epson_projector_power_state").state == "standby"
    assert hass.states.get("select.epson_projector_input").state == STATE_UNAVAILABLE
    assert hass.states.get("button.epson_projector_switch_to_hdmi1").state == STATE_UNAVAILABLE
    # Only PWR (and ERR occasionally) is polled while off.
    projector.received.clear()
    await refresh(hass, setup_entry)
    assert projector.received == ["PWR?"]


async def test_warming_and_cooling(
    hass: HomeAssistant, setup_entry: MockConfigEntry, projector: FakeProjector
) -> None:
    projector.state["PWR"] = "02"
    await refresh(hass, setup_entry)
    assert hass.states.get(MP).state == STATE_ON
    assert hass.states.get("sensor.epson_projector_power_state").state == "warming_up"
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "media_player", "turn_off", {"entity_id": MP}, blocking=True
        )
    projector.state["PWR"] = "03"
    await refresh(hass, setup_entry)
    assert hass.states.get(MP).state == STATE_OFF
    assert hass.states.get("sensor.epson_projector_power_state").state == "cooling_down"


async def test_timeouts_keep_last_state(
    hass: HomeAssistant, setup_entry: MockConfigEntry, projector: FakeProjector
) -> None:
    """A few missed replies never flip the projector off."""
    for _ in range(MAX_CONSECUTIVE_FAILURES - 1):
        projector.drop_next = 1
        await refresh(hass, setup_entry)
        assert hass.states.get(MP).state == STATE_ON
        assert hass.states.get("select.epson_projector_input").state == "HDMI1"

    projector.drop_next = 1
    await refresh(hass, setup_entry)
    assert hass.states.get(MP).state == STATE_UNAVAILABLE

    await refresh(hass, setup_entry)
    assert hass.states.get(MP).state == STATE_ON


async def test_bridge_restart(
    hass: HomeAssistant, setup_entry: MockConfigEntry, projector: FakeProjector
) -> None:
    await projector.disconnect_all()
    await refresh(hass, setup_entry)
    assert hass.states.get(MP).state == STATE_ON
    assert setup_entry.runtime_data.client.stats.connects == 2


async def test_never_off_with_other_client(
    hass: HomeAssistant, setup_entry: MockConfigEntry, projector: FakeProjector
) -> None:
    """The scenario that breaks the core integration."""
    other = OtherClient(
        projector.port,
        ["PWR?", "SOURCE?", "VOLUME?", "LAMP?", "CMODE?", "ERR?"],
        interval=0.02,
    )
    await other.start()
    try:
        for _ in range(10):
            await refresh(hass, setup_entry)
            assert hass.states.get(MP).state == STATE_ON
            assert hass.states.get(MP).attributes["source"] == "HDMI1"
            assert hass.states.get("sensor.epson_projector_power_state").state == "on"
    finally:
        await other.stop()


async def test_lone_foreign_err_keeps_value(
    hass: HomeAssistant, setup_entry: MockConfigEntry, projector: FakeProjector
) -> None:
    """A single unexplained ERR does not blank a select; two in a row do."""
    original = projector.respond

    def cmode_err(command: str) -> bytes:
        if command == "CMODE?":
            return b"ERR\r:"
        return original(command)

    projector.respond = cmode_err
    await refresh(hass, setup_entry)
    assert hass.states.get("select.epson_projector_color_mode").state == "Code 06"
    await refresh(hass, setup_entry)
    assert hass.states.get("select.epson_projector_color_mode").state == STATE_UNAVAILABLE


async def test_select_source_sends_code(
    hass: HomeAssistant, setup_entry: MockConfigEntry, projector: FakeProjector
) -> None:
    await hass.services.async_call(
        "media_player",
        "select_source",
        {"entity_id": MP, "source": "HDMI2"},
        blocking=True,
    )
    assert "SOURCE A0" in projector.received
    assert "HDMI2" not in projector.received
    await hass.async_block_till_done()
    await refresh(hass, setup_entry)
    assert hass.states.get(MP).attributes["source"] == "HDMI2"

    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": "button.epson_projector_switch_to_hdmi1"},
        blocking=True,
    )
    assert projector.received[projector.received.index("SOURCE A0") + 1 :].count("SOURCE 30") == 1


async def test_select_entities(
    hass: HomeAssistant, setup_entry: MockConfigEntry, projector: FakeProjector
) -> None:
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.epson_projector_lamp_mode", "option": "Eco"},
        blocking=True,
    )
    assert "LUMINANCE 01" in projector.received
    # Seen codes stay selectable even without a name.
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.epson_projector_color_mode", "option": "Code 06"},
        blocking=True,
    )
    assert "CMODE 06" in projector.received


async def test_rejected_command_raises(
    hass: HomeAssistant, setup_entry: MockConfigEntry, projector: FakeProjector
) -> None:
    projector.valid["SOURCE"] = {"30"}
    with pytest.raises(HomeAssistantError, match="rejected"):
        await hass.services.async_call(
            "media_player",
            "select_source",
            {"entity_id": MP, "source": "HDMI2"},
            blocking=True,
        )
    assert hass.states.get(MP).state == STATE_ON


async def test_turn_on_off(
    hass: HomeAssistant, setup_entry: MockConfigEntry, projector: FakeProjector
) -> None:
    await hass.services.async_call(
        "media_player", "turn_off", {"entity_id": MP}, blocking=True
    )
    assert "PWR OFF" in projector.received
    await refresh(hass, setup_entry)
    assert hass.states.get(MP).state == STATE_OFF
    await hass.services.async_call(
        "media_player", "turn_on", {"entity_id": MP}, blocking=True
    )
    assert "PWR ON" in projector.received


async def test_send_command_action(
    hass: HomeAssistant, setup_entry: MockConfigEntry, projector: FakeProjector
) -> None:
    result = await hass.services.async_call(
        DOMAIN, "send_command", {"command": "PWR?"}, blocking=True, return_response=True
    )
    assert result == {"command": "PWR?", "response": "PWR=01"}
    result = await hass.services.async_call(
        DOMAIN,
        "send_command",
        {"command": "VOLUME?", "config_entry_id": setup_entry.entry_id},
        blocking=True,
        return_response=True,
    )
    assert result["response"] == "ERR"


async def test_setup_retries_when_bridge_down(hass: HomeAssistant, fast_client, socket_enabled) -> None:
    fake = FakeProjector()
    await fake.start()
    port = fake.port
    await fake.stop()
    entry = MockConfigEntry(
        domain=DOMAIN, data={"host": "127.0.0.1", "port": port}, unique_id="x"
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_diagnostics(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    from custom_components.epson_escvp21.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    diag = await async_get_config_entry_diagnostics(hass, setup_entry)
    assert diag["entry"]["data"]["host"] == "**REDACTED**"
    assert diag["state"]["power"] == "01"
    assert diag["state"]["serial"] == "**REDACTED**"
    assert diag["client"]["connected"] is True


async def test_serial_learned_later(
    hass: HomeAssistant, projector: FakeProjector, fast_client
) -> None:
    """An entry made without a serial picks it up when the projector answers."""
    sno = projector.state.pop("SNO")
    projector.state["PWR"] = "00"
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Epson projector",
        unique_id=f"127.0.0.1:{projector.port}",
        data={"host": "127.0.0.1", "port": projector.port},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.unique_id == f"127.0.0.1:{projector.port}"

    projector.received.clear()
    await refresh(hass, entry)
    assert "SNO?" not in projector.received  # not retried while still off

    projector.state["SNO"] = sno
    projector.state["PWR"] = "01"
    await refresh(hass, entry)
    assert entry.unique_id == sno
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, entry.entry_id), entry.entry_id
    )
    assert device.serial_number == sno
    await hass.config_entries.async_unload(entry.entry_id)
