"""Send announcement embeds through a Discord webhook."""

from __future__ import annotations

import time
from collections.abc import Callable
from urllib.parse import urlsplit

import requests

from maple_parser import Announcement


class DiscordSendError(RuntimeError):
    """Raised when Discord does not accept a webhook message."""


MAX_ATTEMPTS = 3
MAX_RETRY_AFTER_SECONDS = 30.0
DISCORD_WEBHOOK_HOSTS = {
    "discord.com",
    "discordapp.com",
    "canary.discord.com",
    "ptb.discord.com",
}


def _truncate(value: object, limit: int) -> str:
    text = str(value)
    if len(text) <= limit:
        return text
    if limit == 1:
        return "…"
    return f"{text[: limit - 1]}…"


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
    session: requests.Session | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    if not webhook_url:
        raise DiscordSendError("缺少 DISCORD_WEBHOOK_URL，無法發送公告。")
    _validate_webhook_url(webhook_url)

    payload = {
        "username": "Maple Classic Bot",
        "embeds": [
            {
                "title": _truncate(announcement.title, 256),
                "url": announcement.url,
                "color": 0xF2A23A,
                "fields": [
                    {
                        "name": _truncate("公告分類", 256),
                        "value": _truncate(announcement.category, 1024),
                        "inline": True,
                    },
                    {
                        "name": _truncate("公告日期", 256),
                        "value": _truncate(announcement.date, 1024),
                        "inline": True,
                    },
                    {
                        "name": _truncate("官方公告連結", 256),
                        "value": _truncate(f"[前往官網查看]({announcement.url})", 1024),
                        "inline": False,
                    },
                ],
                "footer": {"text": _truncate("新楓之谷：經典版官方公告", 2048)},
            }
        ],
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
