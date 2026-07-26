"""Maple Classic Discord Center entry point."""

from __future__ import annotations

import logging
import sys

from config import Config
from discord_sender import DiscordSendError, send_announcement
from maple_parser import MapleParserError, fetch_announcements
from state_store import StateStoreError, load_sent_ids, save_sent_ids

LOGGER = logging.getLogger("maple-classic-discord-center")


def run(config: Config) -> int:
    announcements = fetch_announcements(
        timeout=config.request_timeout, user_agent=config.user_agent
    )
    current_ids = {item.announcement_id for item in announcements}
    sent_ids = load_sent_ids(config.state_file)

    if sent_ids is None:
        if not config.test_mode:
            save_sent_ids(config.state_file, current_ids)
            LOGGER.info("首次執行：已建立 %d 篇公告基準，本次不發送。", len(current_ids))
            return 0

        latest = announcements[0]
        LOGGER.info("TEST_MODE：發送目前最新公告 %s。", latest.announcement_id)
        send_announcement(
            config.discord_webhook_url or "",
            latest,
            timeout=config.request_timeout,
            user_agent=config.user_agent,
            thumbnail_url=config.maple_thumbnail_url,
        )
        save_sent_ids(config.state_file, current_ids)
        LOGGER.info("測試公告發送成功，已建立目前公告基準。")
        return 0

    if config.test_mode:
        latest = announcements[0]
        LOGGER.info("TEST_MODE：發送目前最新公告 %s（可重複測試）。", latest.announcement_id)
        send_announcement(
            config.discord_webhook_url or "",
            latest,
            timeout=config.request_timeout,
            user_agent=config.user_agent,
            thumbnail_url=config.maple_thumbnail_url,
        )
        LOGGER.info("測試公告發送成功；既有狀態不變。")
        return 0

    new_items = [item for item in announcements if item.announcement_id not in sent_ids]
    if not new_items:
        LOGGER.info("沒有新公告。")
        return 0

    # API is newest-first; send oldest-first for a natural Discord timeline.
    for item in reversed(new_items):
        LOGGER.info("正在發送公告 %s：%s", item.announcement_id, item.title)
        send_announcement(
            config.discord_webhook_url or "",
            item,
            timeout=config.request_timeout,
            user_agent=config.user_agent,
            thumbnail_url=config.maple_thumbnail_url,
        )
        sent_ids.add(item.announcement_id)
        save_sent_ids(config.state_file, sent_ids)
        LOGGER.info("公告 %s 發送成功並已記錄。", item.announcement_id)
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        return run(Config.from_env())
    except (ValueError, MapleParserError, DiscordSendError, StateStoreError) as exc:
        LOGGER.error("%s", exc)
        return 1
    except Exception:
        LOGGER.exception("發生未預期錯誤；狀態檔未被主動更新。")
        return 1


if __name__ == "__main__":
    sys.exit(main())
