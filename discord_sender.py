"""Send announcement embeds through a Discord webhook."""

from __future__ import annotations

import time
from collections.abc import Callable
from urllib.parse import urlsplit

import requests

from config import validate_https_image_url
from maple_parser import Announcement


class DiscordSendError(RuntimeError):
    """Raised when Discord does not accept a webhook message."""


MAX_ATTEMPTS = 3
MAX_RETRY_AFTER_SECONDS = 30.0
CATEGORY_COLORS = {
    "活動": 0xB53A2D,
    "更新": 0xD8B400,
    "重要": 0x2D63A8,
    "綜合": 0x6F6F6F,
}
CATEGORY_ICONS = {
    "活動": "📅",
    "更新": "🔧",
    "重要": "🚨",
    "綜合": "📢",
}
DEFAULT_CATEGORY_COLOR = 0x95A5A6
DEFAULT_CATEGORY_ICON = "📢"
DISCORD_WEBHOOK_HOSTS = {
    "discord.com",
    "discordapp.com",
    "canary.discord.com",
    "ptb.discord.com",
}


def _normalize_category(category: str | None) -> str:
    """Return a category in its canonical form for display and lookup."""
    return (category or "").strip()


def get_category_color(category: str | None) -> int:
    """Return the Discord embed color for an announcement category."""
    return CATEGORY_COLORS.get(_normalize_category(category), DEFAULT_CATEGORY_COLOR)


def get_category_icon(category: str | None) -> str:
    """Return the icon for an announcement category."""
    return CATEGORY_ICONS.get(_normalize_category(category), DEFAULT_CATEGORY_ICON)


def get_category_display(category: str | None) -> str:
    """Return the category label prefixed with its Discord-friendly icon."""
    normalized_category = _normalize_category(category)
    if not normalized_category:
        return get_category_icon(category)
    return f"{get_category_icon(category)} {normalized_category}"


def _truncate(value: object, limit: int) -> str:
    text = str(value)
    if len(text) <= limit:
        return text
    if limit == 1:
        return "…"
    return f"{text[: limit - 1]}…"


def get_embed_title(category: str | None, title: object) -> str:
    """Return an icon-prefixed embed title within Discord's 256-character limit."""
    prefix = f"{get_category_icon(category)} "
    return f"{prefix}{_truncate(title, 256 - len(prefix))}"


def _validate_webhook_url(webhook_url: str) -> None:
    try:
        parsed = urlsplit(webhook_url)
    except (TypeError, ValueError) as exc:
        raise DiscordSendError("Discord Webhook URL 格式不正確。") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname not in DISCORD_WEBHOOK_HOSTS
        or not parsed.path.startswith("/api/webhooks/")
        or not parsed.path.removeprefix("/api/webhooks/").strip("/")
    ):
        raise DiscordSendError("Discord Webhook URL 格式不正確。")


def _response_detail(response: object, webhook_url: str) -> str:
    status_code = getattr(response, "status_code", None)
    text = str(getattr(response, "text", "")).replace(webhook_url, "[REDACTED]")[:200]
    if status_code is None:
        return ""
    return f"（HTTP {status_code}: {text}）"


def _retry_after(response: object, fallback: float) -> float:
    try:
        value = float(response.json().get("retry_after"))
        if value >= 0:
            return min(value, MAX_RETRY_AFTER_SECONDS)
    except (AttributeError, TypeError, ValueError, requests.RequestException):
        pass
    return min(fallback, MAX_RETRY_AFTER_SECONDS)


def send_announcement(
    webhook_url: str,
    announcement: Announcement,
    *,
    timeout: float = 15,
    user_agent: str,
    thumbnail_url: str | None = None,
    session: requests.Session | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    if not webhook_url:
        raise DiscordSendError("缺少 DISCORD_WEBHOOK_URL，無法發送公告。")
    _validate_webhook_url(webhook_url)
    try:
        thumbnail_url = validate_https_image_url(thumbnail_url)
    except ValueError as exc:
        raise DiscordSendError(str(exc)) from exc

    embed = {
        "author": {"name": _truncate("新楓之谷：經典版官方消息", 256)},
        "title": get_embed_title(announcement.category, announcement.title),
        "url": announcement.url,
        "color": get_category_color(announcement.category),
        "fields": [
            {
                "name": _truncate("🏷️ 公告分類", 256),
                "value": _truncate(_normalize_category(announcement.category), 1024),
                "inline": True,
            },
            {
                "name": _truncate("📅 公告日期", 256),
                "value": _truncate(announcement.date, 1024),
                "inline": True,
            },
        ],
        "footer": {
            "text": _truncate(
                "Maple Classic Discord Center｜羽田製作\n"
                f"公告 ID：{announcement.announcement_id}",
                2048,
            )
        },
    }
    if thumbnail_url:
        embed["thumbnail"] = {"url": thumbnail_url}
        embed["author"]["icon_url"] = thumbnail_url
        embed["footer"]["icon_url"] = thumbnail_url

    payload = {
        "username": "Maple Classic Bot",
        "embeds": [embed],
        "allowed_mentions": {"parse": []},
    }
    client = session or requests.Session()
    owns_session = session is None
    try:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = client.post(
                    webhook_url,
                    json=payload,
                    headers={"User-Agent": user_agent},
                    timeout=timeout,
                )
                status_code = getattr(response, "status_code", None)
                if status_code is None:
                    response.raise_for_status()
                    return
                if 200 <= status_code < 300:
                    return
                retryable = status_code == 429 or status_code in {500, 502, 503, 504}
                if not retryable:
                    raise DiscordSendError(
                        f"Discord Webhook 發送失敗：{_response_detail(response, webhook_url)}"
                    )
                error = DiscordSendError(
                    f"Discord Webhook 發送失敗：{_response_detail(response, webhook_url)}"
                )
                delay = _retry_after(response, float(2 ** (attempt - 1))) if status_code == 429 else float(2 ** (attempt - 1))
            except (requests.ConnectionError, requests.Timeout) as exc:
                error = DiscordSendError("Discord Webhook 發送失敗：連線或 timeout 錯誤。")
                delay = float(2 ** (attempt - 1))
            except requests.RequestException as exc:
                response = getattr(exc, "response", None)
                raise DiscordSendError(
                    f"Discord Webhook 發送失敗：請求錯誤{_response_detail(response, webhook_url)}"
                ) from exc

            if attempt == MAX_ATTEMPTS:
                raise error
            sleep(min(delay, MAX_RETRY_AFTER_SECONDS))
    finally:
        if owns_session:
            client.close()
