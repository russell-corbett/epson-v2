"""Standby polling, restored sensor values and persisted select codes."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache_with_extra_data,
)

from custom_components.epson_escvp21.const import (
    DOMAIN,
    KEY_ERROR,
    KEY_LAMP,
    LAMP_POLL_INTERVAL_OFF,
)
from custom_components.epson_escvp21.coordinator import codes_store_key

from .fake_projector import FakeProjector

LAMP = "sensor.epson_projector_lamp_hours"
ERROR = "sensor.epson_projector_error"
CMODE = "select.epson_projector_color_mode"
ASPECT = "select.epson_projector_aspect"


async def _setup(hass: HomeAssistant, projector: FakeProjector) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Epson projector",
        entry_id="test_entry",
        unique_id="TZ2F380077L",
        data={"host": "127.0.0.1", "port": projector.port},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _refresh(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()


async def test_standby_startup_reads_lamp(
    hass: HomeAssistant, projector: FakeProjector, fast_client
) -> None:
    """In standby LAMP? answers, so lamp hours are known straight away."""
    projector.state["PWR"] = "00"
    entry = await _setup(hass, projector)
    assert projector.received[:3] == ["PWR?", "ERR?", "LAMP?"]
    assert hass.states.get(LAMP).state == "1275"
    # ERR? is rejected in standby (as on the real 6030UB) and nothing is known.
    assert hass.states.get(ERROR).state == STATE_UNKNOWN

    # Neither is retried on the next cycles...
    projector.received.clear()
    await _refresh(hass, entry)
    await _refresh(hass, entry)
    assert projector.received == ["PWR?", "PWR?"]

    # ...until their standby intervals pass.
    coordinator = entry.runtime_data
    coordinator._last_poll[KEY_LAMP] -= LAMP_POLL_INTERVAL_OFF
    coordinator._last_poll[KEY_ERROR] -= timedelta(minutes=5)
    projector.state["LAMP"] = "1276"
    await _refresh(hass, entry)
    assert projector.received[2:] == ["PWR?", "ERR?", "LAMP?"]
    assert hass.states.get(LAMP).state == "1276"
    await hass.config_entries.async_unload(entry.entry_id)


async def test_lamp_err_in_standby_is_graceful(
    hass: HomeAssistant, projector: FakeProjector, fast_client
) -> None:
    """A model that rejects LAMP? in standby keeps the last value."""
    projector.state["PWR"] = "01"
    entry = await _setup(hass, projector)
    assert hass.states.get(LAMP).state == "1275"
    assert hass.states.get(ERROR).state == "OK"

    projector.state["PWR"] = "00"
    del projector.state["LAMP"]  # LAMP? -> ERR from now on
    coordinator = entry.runtime_data
    coordinator._last_poll[KEY_LAMP] -= LAMP_POLL_INTERVAL_OFF
    for _ in range(3):
        await _refresh(hass, entry)
    assert "LAMP?" in projector.received[-4:]
    assert "ERR?" in projector.received
    # Both rejected while off; last known values are kept.
    assert hass.states.get(LAMP).state == "1275"
    assert hass.states.get(ERROR).state == "OK"
    assert coordinator.last_update_success
    await hass.config_entries.async_unload(entry.entry_id)


async def test_sensors_restore_after_restart(
    hass: HomeAssistant, projector: FakeProjector, fast_client
) -> None:
    """Without a live answer, lamp hours and error show their pre-restart values."""
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(LAMP, "1270"),
                {"native_value": 1270, "native_unit_of_measurement": "h"},
            ),
            (State(ERROR, "OK"), {"native_value": "OK", "native_unit_of_measurement": None}),
        ],
    )
    projector.state["PWR"] = "00"
    del projector.state["LAMP"]
    entry = await _setup(hass, projector)
    assert hass.states.get(LAMP).state == "1270"
    assert hass.states.get(ERROR).state == "OK"
    await hass.config_entries.async_unload(entry.entry_id)


async def test_live_value_beats_restored(
    hass: HomeAssistant, projector: FakeProjector, fast_client
) -> None:
    mock_restore_cache_with_extra_data(
        hass,
        [(State(LAMP, "1000"), {"native_value": 1000, "native_unit_of_measurement": "h"})],
    )
    projector.state["PWR"] = "00"
    entry = await _setup(hass, projector)
    assert hass.states.get(LAMP).state == "1275"
    await hass.config_entries.async_unload(entry.entry_id)


async def test_learned_codes_are_saved(
    hass: HomeAssistant, projector: FakeProjector, fast_client, hass_storage
) -> None:
    entry = await _setup(hass, projector)
    # Saved on unload (and by a delayed save / at shutdown while running).
    await hass.config_entries.async_unload(entry.entry_id)
    saved = hass_storage[codes_store_key(entry.entry_id)]["data"]["seen_codes"]
    assert saved["CMODE"] == ["06"]
    assert saved["ASPECT"] == ["00"]
    assert saved["SOURCE"] == ["30"]
    assert "ERR" not in saved

    # Removing the entry deletes the file.
    await hass.config_entries.async_remove(entry.entry_id)
    assert codes_store_key(entry.entry_id) not in hass_storage


async def test_learned_codes_restored_while_off(
    hass: HomeAssistant, projector: FakeProjector, fast_client, hass_storage
) -> None:
    """After a restart in standby, the selects still offer learned codes."""
    hass_storage[codes_store_key("test_entry")] = {
        "version": 1,
        "minor_version": 1,
        "key": codes_store_key("test_entry"),
        "data": {"seen_codes": {"CMODE": ["06", "07"], "ASPECT": ["00"]}},
    }
    projector.state["PWR"] = "00"
    entry = await _setup(hass, projector)
    registry = er.async_get(hass)
    assert registry.async_get(CMODE).capabilities["options"] == ["Code 06", "Code 07"]
    assert registry.async_get(ASPECT).capabilities["options"] == ["Code 00"]

    # Once on, a newly seen code is added alongside the saved ones.
    projector.state["PWR"] = "01"
    projector.state["CMODE"] = "15"
    await _refresh(hass, entry)
    state = hass.states.get(CMODE)
    assert state.state == "Code 15"
    assert state.attributes["options"] == ["Code 06", "Code 07", "Code 15"]
    await hass.config_entries.async_unload(entry.entry_id)


async def test_static_names_used_as_options(
    hass: HomeAssistant, projector: FakeProjector, fast_client, monkeypatch
) -> None:
    """Named codes from const.py are offered from startup; unknown ones as Code XX."""
    from custom_components.epson_escvp21 import select as select_mod

    names = {"06": "Dynamic", "07": "Natural"}
    descriptions = tuple(
        d if d.key != "CMODE" else type(d)(**{**d.__dict__, "names": names})
        for d in select_mod.SELECTS
    )
    monkeypatch.setattr(select_mod, "SELECTS", descriptions)
    projector.state["CMODE"] = "15"
    entry = await _setup(hass, projector)
    state = hass.states.get(CMODE)
    assert state.state == "Code 15"
    assert state.attributes["options"] == ["Dynamic", "Natural", "Code 15"]
    await hass.services.async_call(
        "select", "select_option", {"entity_id": CMODE, "option": "Natural"}, blocking=True
    )
    assert "CMODE 07" in projector.received
    await hass.config_entries.async_unload(entry.entry_id)
