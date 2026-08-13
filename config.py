"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

load_dotenv()


def _as_bool(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def validate_https_image_url(value: str | None) -> str | None:
    """Return an optional HTTPS image URL, rejecting unsafe or malformed values."""
    if value is None or not value.strip():
        return None

    url = value.strip()
    try:
        parsed = urlsplit(url)
    except (TypeError, ValueError) as exc:
        raise ValueError("MAPLE_THUMBNAIL_URL 必須是合法的 HTTPS 圖片網址") from exc
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("MAPLE_THUMBNAIL_URL 必須是含 hostname 的 HTTPS 圖片網址")
    return url


def validate_discord_spacer_emoji(value: str | None) -> str | None:
    """Return an optional static Discord custom emoji used as a visual spacer."""
    if value is None or not value.strip():
        return None

    emoji = value.strip()
    if not re.fullmatch(r"<:[A-Za-z0-9_]+:\d+>", emoji):
        raise ValueError(
            "DISCORD_SPACER_EMOJI 必須是 <:name:numeric_id> 格式的靜態 Discord 自訂 Emoji"
        )
    return emoji


@dataclass(frozen=True)
class Config:
    discord_webhook_url: str | None
    test_mode: bool
    state_file: Path
    request_timeout: float
    user_agent: str
    maple_thumbnail_url: str | None = None
    discord_spacer_emoji: str | None = None
    discord_bot_token: str | None = None

    @classmethod
    def from_env(cls) -> "Config":
        timeout_raw = os.getenv("REQUEST_TIMEOUT", "15")
        try:
            timeout = float(timeout_raw)
        except ValueError as exc:
            raise ValueError("REQUEST_TIMEOUT 必須是數字。") from exc
        if timeout <= 0:
            raise ValueError("REQUEST_TIMEOUT 必須大於 0。")

        return cls(
            discord_webhook_url=os.getenv("DISCORD_WEBHOOK_URL") or None,
            test_mode=_as_bool(os.getenv("TEST_MODE")),
            state_file=Path(os.getenv("STATE_FILE", "data/state.json")),
            request_timeout=timeout,
            user_agent=os.getenv(
                "USER_AGENT", "MapleClassicDiscordCenter/1.0 (+GitHub Actions)"
            ),
            maple_thumbnail_url=validate_https_image_url(
                os.getenv("MAPLE_THUMBNAIL_URL")
            ),
            discord_spacer_emoji=validate_discord_spacer_emoji(
                os.getenv("DISCORD_SPACER_EMOJI")
            ),
            discord_bot_token=(os.getenv("DISCORD_BOT_TOKEN") or "").strip()
            or None,
        )
