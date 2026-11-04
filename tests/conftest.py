"""Fixtures."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from unittest.mock import patch

import pytest

from .fake_projector import FakeProjector

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations."""
    return


@pytest.fixture(autouse=True)
def no_udp_listener():
    """Never bind UDP 4352 in tests."""
    with patch(
        "custom_components.aetherion.async_get_listener", return_value=None
    ) as mock:
        yield mock


@pytest.fixture
async def projector(socket_enabled: None) -> AsyncGenerator[FakeProjector]:
    """A running fake projector."""
    fake = FakeProjector()
    await fake.start()
    yield fake
    await fake.stop()
