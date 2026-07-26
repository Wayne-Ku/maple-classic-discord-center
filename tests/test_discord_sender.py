import pytest
import requests

from discord_sender import DiscordSendError, get_category_color, send_announcement
from maple_parser import Announcement

WEBHOOK = "https://discord.com/api/webhooks/123456/test-token"


class FakeResponse:
    def __init__(self, status_code=204, text="", json_body=None):
        self.status_code = status_code
        self.text = text
        self.json_body = json_body if json_body is not None else {}

    def json(self):
        return self.json_body


class FakeSession:
    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.calls = []
        self.closed = False

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def close(self):
        self.closed = True


def announcement(title="標題", category="重要", date="2026/07/23", url="https://example.com/1"):
    return Announcement("1", category, title, date, url)


@pytest.mark.parametrize(
    ("category", "expected_color"),
    [
        ("活動", 0x57F287),
        ("更新", 0x5865F2),
        ("重要", 0xED4245),
        ("綜合", 0x95A5A6),
        ("未知分類", 0x95A5A6),
        ("", 0x95A5A6),
        (None, 0x95A5A6),
        (" 活動 ", 0x57F287),
    ],
)
def test_get_category_color(category, expected_color):
    assert get_category_color(category) == expected_color


@pytest.mark.parametrize(
    ("category", "expected_color"),
    [
        ("活動", 0x57F287),
        ("更新", 0x5865F2),
        ("重要", 0xED4245),
        ("綜合", 0x95A5A6),
        ("未知分類", 0x95A5A6),
    ],
)
def test_payload_embed_color_matches_category(category, expected_color):
    session = FakeSession([FakeResponse(204)])
    send_announcement(
        WEBHOOK,
        announcement(category=category),
        user_agent="test",
        session=session,
    )
    assert session.calls[0][1]["json"]["embeds"][0]["color"] == expected_color


def test_normal_204_success_payload_and_mentions():
    session = FakeSession([FakeResponse(204)])
    send_announcement(WEBHOOK, announcement(), user_agent="test", session=session)
    payload = session.calls[0][1]["json"]
    assert "discord" not in payload["username"].lower()
    assert payload["allowed_mentions"] == {"parse": []}
    assert payload["embeds"][0]["title"] == "標題"
    assert {field["name"] for field in payload["embeds"][0]["fields"]} == {
        "公告分類",
        "公告日期",
        "官方公告連結",
    }
    assert session.closed is False


@pytest.mark.parametrize(
    "url",
    ["", "http://discord.com/api/webhooks/1/x", "https://example.com/api/webhooks/1/x", "https://discord.com/not-webhook"],
)
def test_invalid_webhook_urls_are_rejected(url):
    with pytest.raises(DiscordSendError):
        send_announcement(url, announcement(), user_agent="test", session=FakeSession([]))


def test_http_400_does_not_retry_or_leak_url():
    session = FakeSession([FakeResponse(400, f"bad request {WEBHOOK}")])
    with pytest.raises(DiscordSendError) as error:
        send_announcement(WEBHOOK, announcement(), user_agent="test", session=session)
    assert len(session.calls) == 1
    assert WEBHOOK not in str(error.value)
    assert "HTTP 400" in str(error.value)


def test_429_retries_using_retry_after():
    session = FakeSession([FakeResponse(429, json_body={"retry_after": 0.25}), FakeResponse(204)])
    sleeps = []
    send_announcement(WEBHOOK, announcement(), user_agent="test", session=session, sleep=sleeps.append)
    assert len(session.calls) == 2
    assert sleeps == [0.25]


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_retryable_server_errors_retry(status):
    session = FakeSession([FakeResponse(status), FakeResponse(204)])
    sleeps = []
    send_announcement(WEBHOOK, announcement(), user_agent="test", session=session, sleep=sleeps.append)
    assert len(session.calls) == 2
    assert sleeps == [1.0]


@pytest.mark.parametrize("error", [requests.Timeout("timeout"), requests.ConnectionError("connection")])
def test_timeout_and_connection_error_retry(error):
    session = FakeSession([error, FakeResponse(204)])
    sleeps = []
    send_announcement(WEBHOOK, announcement(), user_agent="test", session=session, sleep=sleeps.append)
    assert len(session.calls) == 2
    assert sleeps == [1.0]


def test_retries_are_limited_and_final_error_is_safe():
    session = FakeSession([FakeResponse(500, "failed")] * 3)
    sleeps = []
    with pytest.raises(DiscordSendError) as error:
        send_announcement(WEBHOOK, announcement(), user_agent="test", session=session, sleep=sleeps.append)
    assert len(session.calls) == 3
    assert sleeps == [1.0, 2.0]
    assert WEBHOOK not in str(error.value)


def test_embed_values_are_safely_truncated():
    session = FakeSession([FakeResponse(204)])
    send_announcement(
        WEBHOOK,
        announcement(title="x" * 300, category="y" * 1100, date="z" * 1100, url="https://example.com/" + "u" * 1100),
        user_agent="test",
        session=session,
    )
    embed = session.calls[0][1]["json"]["embeds"][0]
    assert len(embed["title"]) == 256
    assert all(field["name"] and len(field["name"]) <= 256 for field in embed["fields"])
    assert all(field["value"] and len(field["value"]) <= 1024 for field in embed["fields"])
    assert embed["footer"]["text"] and len(embed["footer"]["text"]) <= 2048


def test_owned_session_is_closed(monkeypatch):
    session = FakeSession([FakeResponse(204)])
    monkeypatch.setattr("discord_sender.requests.Session", lambda: session)
    send_announcement(WEBHOOK, announcement(), user_agent="test")
    assert session.closed is True
