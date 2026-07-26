import pytest
import requests

from discord_sender import DiscordSendError, get_category_color, get_category_display, send_announcement
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
        ("活動", 0xB53A2D),
        ("更新", 0xD8B400),
        ("重要", 0x2D63A8),
        ("綜合", 0x6F6F6F),
        ("未知分類", 0x95A5A6),
        ("", 0x95A5A6),
        (None, 0x95A5A6),
        (" 活動 ", 0xB53A2D),
    ],
)
def test_get_category_color(category, expected_color):
    assert get_category_color(category) == expected_color


@pytest.mark.parametrize(
    ("category", "expected_color"),
    [
        ("活動", 0xB53A2D),
        ("更新", 0xD8B400),
        ("重要", 0x2D63A8),
        ("綜合", 0x6F6F6F),
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


@pytest.mark.parametrize(
    ("category", "expected_display"),
    [
        ("活動", "📅 活動"),
        ("更新", "🔧 更新"),
        ("重要", "🚨 重要"),
        ("綜合", "📢 綜合"),
        ("Unknown", "📢 Unknown"),
        (" 活動 ", "📅 活動"),
    ],
)
def test_get_category_display(category, expected_display):
    assert get_category_display(category) == expected_display


@pytest.mark.parametrize(
    ("category", "expected_color", "expected_display"),
    [
        ("活動", 0xB53A2D, "活動"),
        ("更新", 0xD8B400, "更新"),
        ("重要", 0x2D63A8, "重要"),
        ("綜合", 0x6F6F6F, "綜合"),
        ("未知分類", 0x95A5A6, "未知分類"),
    ],
)
def test_payload_embed_category_matches_category(category, expected_color, expected_display):
    session = FakeSession([FakeResponse(204)])
    send_announcement(
        WEBHOOK,
        announcement(category=category),
        user_agent="test",
        session=session,
    )
    embed = session.calls[0][1]["json"]["embeds"][0]
    assert embed["color"] == expected_color
    assert f"公告分類：{expected_display}" in embed["description"]


@pytest.mark.parametrize(
    ("category", "expected_prefix"),
    [
        ("活動", "📅 "),
        ("更新", "🔧 "),
        ("重要", "🚨 "),
        ("綜合", "📢 "),
        ("Unknown", "📢 "),
        ("", "📢 "),
        (" 活動 ", "📅 "),
    ],
)
def test_payload_embed_title_starts_with_category_icon(category, expected_prefix):
    session = FakeSession([FakeResponse(204)])
    send_announcement(
        WEBHOOK,
        announcement(title="公告標題", category=category),
        user_agent="test",
        session=session,
    )
    assert session.calls[0][1]["json"]["embeds"][0]["title"] == f"{expected_prefix}公告標題"


def test_normal_204_success_payload_and_mentions():
    session = FakeSession([FakeResponse(204)])
    send_announcement(WEBHOOK, announcement(), user_agent="test", session=session)
    payload = session.calls[0][1]["json"]
    assert "discord" not in payload["username"].lower()
    assert payload["allowed_mentions"] == {"parse": []}
    assert payload["embeds"][0]["title"] == "🚨 標題"
    embed = payload["embeds"][0]
    assert "fields" not in embed
    assert embed["description"] == "🏷️ 公告分類：重要     📅 公告日期：2026/07/23\n\n"
    assert "公告分類：" in embed["description"]
    assert "公告日期：" in embed["description"]
    assert "官方公告" not in embed["description"]
    assert "公告編號" not in embed["description"]
    assert embed["author"] == {"name": "新楓之谷：經典版官方消息"}
    assert embed["footer"] == {
        "text": "Maple Classic Discord Center｜羽田製作\n公告 ID：1"
    }
    assert "thumbnail" not in embed
    assert session.closed is False


def test_spacer_emoji_is_not_used_in_description_layout():
    session = FakeSession([FakeResponse(204)])
    emoji = "<:blank:123456789012345678>"

    send_announcement(
        WEBHOOK,
        announcement(category="活動"),
        user_agent="test",
        spacer_emoji=emoji,
        session=session,
    )

    embed = session.calls[0][1]["json"]["embeds"][0]
    assert "fields" not in embed
    assert embed["description"] == "🏷️ 公告分類：活動     📅 公告日期：2026/07/23\n\n"
    assert emoji not in embed["description"]
    assert embed["url"] == "https://example.com/1"
    assert embed["footer"]["text"] == "Maple Classic Discord Center｜羽田製作\n公告 ID：1"


def test_thumbnail_url_adds_embed_author_and_footer_icons():
    session = FakeSession([FakeResponse(204)])
    thumbnail_url = "https://cdn.example.com/maple-logo"

    send_announcement(
        WEBHOOK,
        announcement(),
        user_agent="test",
        thumbnail_url=thumbnail_url,
        session=session,
    )

    embed = session.calls[0][1]["json"]["embeds"][0]
    assert embed["thumbnail"] == {"url": thumbnail_url}
    assert embed["author"]["icon_url"] == thumbnail_url
    assert embed["footer"]["icon_url"] == thumbnail_url


@pytest.mark.parametrize(
    "thumbnail_url",
    ["http://cdn.example.com/logo", "https:///logo", "javascript:alert(1)"],
)
def test_invalid_thumbnail_url_is_rejected_without_leaking_url(thumbnail_url):
    with pytest.raises(DiscordSendError) as error:
        send_announcement(
            WEBHOOK,
            announcement(),
            user_agent="test",
            thumbnail_url=thumbnail_url,
            session=FakeSession([]),
        )

    assert thumbnail_url not in str(error.value)


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
    assert len(embed["author"]["name"]) <= 256
    assert "fields" not in embed
    assert embed["description"] and len(embed["description"]) <= 4096
    assert embed["footer"]["text"] and len(embed["footer"]["text"]) <= 2048


def test_embed_title_truncation_preserves_category_icon():
    session = FakeSession([FakeResponse(204)])
    send_announcement(
        WEBHOOK,
        announcement(title="x" * 300, category="活動"),
        user_agent="test",
        session=session,
    )
    title = session.calls[0][1]["json"]["embeds"][0]["title"]
    assert title == f"📅 {'x' * 253}…"
    assert len(title) == 256
    assert title.startswith("📅 ")


def test_owned_session_is_closed(monkeypatch):
    session = FakeSession([FakeResponse(204)])
    monkeypatch.setattr("discord_sender.requests.Session", lambda: session)
    send_announcement(WEBHOOK, announcement(), user_agent="test")
    assert session.closed is True
