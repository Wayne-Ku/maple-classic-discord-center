"""Unit tests must never send real HTTP requests."""
import pytest
import requests


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail("Unexpected real network access; supply a fake session")

    monkeypatch.setattr(requests.sessions.Session, "request", blocked)
