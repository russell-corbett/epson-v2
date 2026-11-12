"""Shared fixtures."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from .fake_projector import FakeProjector


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading custom_components in every test."""
    return


@pytest.fixture
async def projector(socket_enabled: None) -> AsyncIterator[FakeProjector]:
    """A running fake projector + bridge."""
    fake = FakeProjector()
    await fake.start()
    yield fake
    await fake.stop()


from functools import partial
from unittest.mock import patch

from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.epson_escvp21.client import EscVp21Client
from custom_components.epson_escvp21.const import DOMAIN

FastClient = partial(
    EscVp21Client,
    query_timeout=0.3,
    set_timeout=0.3,
    err_grace=0.05,
    backoff_min=0,
    backoff_max=0,
)


@pytest.fixture
def fast_client():
    """Use short timeouts everywhere a client is created."""
    with (
        patch("custom_components.epson_escvp21.EscVp21Client", FastClient),
        patch("custom_components.epson_escvp21.config_flow.EscVp21Client", FastClient),
    ):
        yield


@pytest.fixture
async def setup_entry(
    hass: HomeAssistant, projector: FakeProjector, fast_client
) -> MockConfigEntry:
    """Set up a config entry pointing at the fake projector."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Epson projector",
        unique_id=f"127.0.0.1:{projector.port}",
        data={CONF_HOST: "127.0.0.1", CONF_PORT: projector.port},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    yield entry
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
