"""Safely remove obsolete webhook copies of one Discord announcement."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import Config
from discord_sender import DISCORD_API_BASE_URL, delete_announcement_messages


def _matching_message_ids(
    config: Config,
    announcement_id: str,
    *,
    session: requests.Session,
) -> tuple[str, ...]:
    if not config.discord_webhook_url or not config.discord_bot_token:
        raise ValueError("清理公告需要 DISCORD_WEBHOOK_URL 與 DISCORD_BOT_TOKEN。")

    webhook = session.get(
        config.discord_webhook_url,
        headers={"User-Agent": config.user_agent},
        timeout=config.request_timeout,
    )
    webhook.raise_for_status()
    metadata = webhook.json()
    webhook_id = str(metadata.get("id") or "")
    channel_id = str(metadata.get("channel_id") or "")
    if not webhook_id.isdecimal() or not channel_id.isdecimal():
        raise ValueError("Discord webhook 回應缺少有效的 webhook/channel ID。")

    response = session.get(
        f"{DISCORD_API_BASE_URL}/channels/{channel_id}/messages",
        params={"limit": 100},
        headers={
            "Authorization": f"Bot {config.discord_bot_token}",
            "User-Agent": config.user_agent,
        },
        timeout=config.request_timeout,
    )
    response.raise_for_status()
    messages = response.json()
    if not isinstance(messages, list):
        raise ValueError("Discord 頻道訊息回應格式不正確。")

    footer_line = f"公告 ID：{announcement_id}"
    matches: list[str] = []
    for message in messages:
        if not isinstance(message, dict) or str(message.get("webhook_id")) != webhook_id:
            continue
        message_id = str(message.get("id") or "")
        embeds = message.get("embeds")
        if not message_id.isdecimal() or not isinstance(embeds, list):
            continue
        if any(
            footer_line
            in str(embed.get("footer", {}).get("text", "")).splitlines()
            for embed in embeds
            if isinstance(embed, dict)
        ):
            matches.append(message_id)
    return tuple(matches)


def cleanup_obsolete_copies(
    config: Config,
    announcement_id: str,
    keep_message_id: str,
    *,
    session: requests.Session | None = None,
) -> tuple[str, ...]:
    if not announcement_id.isdecimal() or not keep_message_id.isdecimal():
        raise ValueError("公告 ID 與保留訊息 ID 必須是數字。")
    client = session or requests.Session()
    owns_session = session is None
    try:
        matches = _matching_message_ids(config, announcement_id, session=client)
        if keep_message_id not in matches:
            raise ValueError("指定保留的 Discord 訊息不符合公告 ID，停止清理。")
        obsolete = tuple(message_id for message_id in matches if message_id != keep_message_id)
        if obsolete:
            delete_announcement_messages(
                config.discord_webhook_url or "",
                obsolete,
                timeout=config.request_timeout,
                user_agent=config.user_agent,
                session=client,
            )
        return obsolete
    finally:
        if owns_session:
            client.close()


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="刪除同公告 ID 的舊版 Discord webhook 訊息。")
    parser.add_argument("--id", required=True, dest="announcement_id")
    parser.add_argument("--keep-message-id", required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        deleted = cleanup_obsolete_copies(
            Config.from_env(),
            args.announcement_id,
            args.keep_message_id,
        )
    except (ValueError, requests.RequestException) as exc:
        print(f"清理失敗：{exc}")
        return 1
    print(f"保留訊息：{args.keep_message_id}")
    print(f"已刪除舊訊息：{len(deleted)}")
    for message_id in deleted:
        print(f"deleted={message_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
