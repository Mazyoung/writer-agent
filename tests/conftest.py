"""Shared test isolation: automated tests must never create a real API client."""

from unittest.mock import MagicMock

import pytest

from src.core import model_provider


@pytest.fixture(autouse=True)
def fake_openai_client(monkeypatch):
    """Keep the entire test suite deterministic and credential-free."""
    client = MagicMock(name="FakeOpenAIClient")
    client.chat.completions.create.side_effect = AssertionError(
        "Automated tests must mock LLM responses; real API access is forbidden"
    )
    constructor = MagicMock(name="OpenAI", return_value=client)
    monkeypatch.setattr(model_provider, "OpenAI", constructor)
    return client
