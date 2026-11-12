"""Config and options flow tests."""

from __future__ import annotations

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.epson_escvp21.const import CONF_SCAN_INTERVAL, DOMAIN

from .fake_projector import FakeProjector


async def _start(hass: HomeAssistant, port: int, name: str = "Theatre"):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": "127.0.0.1", "port": port, "name": name}
    )


async def test_flow_projector_on(
    hass: HomeAssistant, projector: FakeProjector, fast_client
) -> None:
    result = await _start(hass, projector.port)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Theatre"
    assert result["data"] == {"host": "127.0.0.1", "port": projector.port}
    assert result["result"].unique_id == "TZ2F380077L"
    # Only read-only queries during setup.
    assert all(c.endswith("?") for c in projector.received)


async def test_flow_projector_off(
    hass: HomeAssistant, projector: FakeProjector, fast_client
) -> None:
    # The 6030UB answers SNO? in standby (seen live).
    projector.state["PWR"] = "04"
    result = await _start(hass, projector.port)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == "TZ2F380077L"
    assert projector.received[:2] == ["PWR?", "SNO?"]
    assert all(c.endswith("?") for c in projector.received)


async def test_flow_cannot_connect(
    hass: HomeAssistant, fast_client, socket_enabled
) -> None:
    fake = FakeProjector()
    await fake.start()
    port = fake.port
    await fake.stop()
    result = await _start(hass, port)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_flow_no_response(
    hass: HomeAssistant, projector: FakeProjector, fast_client
) -> None:
    projector.drop_next = 10
    result = await _start(hass, projector.port)
    assert result["errors"] == {"base": "no_response"}


async def test_flow_already_configured(
    hass: HomeAssistant, projector: FakeProjector, fast_client
) -> None:
    MockConfigEntry(
        domain=DOMAIN,
        unique_id="TZ2F380077L",
        data={"host": "10.0.0.1", "port": 1},
    ).add_to_hass(hass)
    result = await _start(hass, projector.port)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_options_flow(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    result = await hass.config_entries.options.async_init(setup_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 30}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert setup_entry.options == {CONF_SCAN_INTERVAL: 30}
    assert setup_entry.runtime_data.update_interval.total_seconds() == 30


async def test_flow_no_serial_falls_back_to_host_port(
    hass: HomeAssistant, projector: FakeProjector, fast_client
) -> None:
    """Models that only answer SNO? while on get host:port first."""
    del projector.state["SNO"]
    projector.state["PWR"] = "00"
    result = await _start(hass, projector.port)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == f"127.0.0.1:{projector.port}"
