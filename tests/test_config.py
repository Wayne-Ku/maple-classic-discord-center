import pytest

from config import Config


def test_thumbnail_url_is_none_when_unset(monkeypatch):
    monkeypatch.delenv("MAPLE_THUMBNAIL_URL", raising=False)

    assert Config.from_env().maple_thumbnail_url is None


def test_thumbnail_url_accepts_https_url(monkeypatch):
    monkeypatch.setenv("MAPLE_THUMBNAIL_URL", "https://cdn.example.com/maple-logo")

    assert Config.from_env().maple_thumbnail_url == "https://cdn.example.com/maple-logo"


@pytest.mark.parametrize(
    "thumbnail_url",
    [
        "http://cdn.example.com/maple-logo.png",
        "https:///maple-logo.png",
        "javascript:alert(1)",
        "data:image/png;base64,abc",
        "file:///tmp/maple-logo.png",
    ],
)
def test_thumbnail_url_rejects_non_https_or_missing_host(monkeypatch, thumbnail_url):
    monkeypatch.setenv("MAPLE_THUMBNAIL_URL", thumbnail_url)

    with pytest.raises(ValueError, match="MAPLE_THUMBNAIL_URL"):
        Config.from_env()


def test_spacer_emoji_is_none_when_unset(monkeypatch):
    monkeypatch.delenv("DISCORD_SPACER_EMOJI", raising=False)

    assert Config.from_env().discord_spacer_emoji is None


def test_spacer_emoji_accepts_static_custom_emoji(monkeypatch):
    emoji = "<:blank:123456789012345678>"
    monkeypatch.setenv("DISCORD_SPACER_EMOJI", emoji)

    assert Config.from_env().discord_spacer_emoji == emoji


@pytest.mark.parametrize(
    "spacer_emoji",
    [
        "blank:123456789012345678",
        "<:blank:not-a-number>",
        "<a:blank:123456789012345678>",
        "🫥",
    ],
)
def test_spacer_emoji_rejects_invalid_formats(monkeypatch, spacer_emoji):
    monkeypatch.setenv("DISCORD_SPACER_EMOJI", spacer_emoji)

    with pytest.raises(ValueError, match="DISCORD_SPACER_EMOJI"):
        Config.from_env()


def test_bot_token_is_none_when_unset(monkeypatch):
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)

    assert Config.from_env().discord_bot_token is None


def test_bot_token_is_trimmed_when_set(monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "  test-bot-token  ")

    assert Config.from_env().discord_bot_token == "test-bot-token"
