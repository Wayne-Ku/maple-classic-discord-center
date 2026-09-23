"""Maple Classic Discord Center entry point."""

from __future__ import annotations

import logging
import sys

from announcement_detail import (
    AnnouncementDetailError,
    fetch_announcement_detail,
)
from config import Config
from discord_sender import (
    DiscordPayloadError,
    DiscordSendError,
    delete_announcement_messages,
    send_announcement,
)
from maple_parser import Announcement, MapleParserError, fetch_announcements
from state_store import (
    AnnouncementState,
    StateStoreError,
    load_state,
    save_state,
)

LOGGER = logging.getLogger("maple-classic-discord-center")


def _send(config: Config, item: Announcement) -> tuple[str, ...]:
    """Send only after official announcement content has parsed successfully."""
    try:
        detail = fetch_announcement_detail(
            item, timeout=config.request_timeout, user_agent=config.user_agent
        )
    except AnnouncementDetailError as exc:
        LOGGER.warning(
            "公告正文解析失敗：ID=%s title=%s reason=%s",
            item.announcement_id,
            item.title,
            exc,
        )
        raise

    kwargs = {
        "timeout": config.request_timeout,
        "user_agent": config.user_agent,
        "thumbnail_url": config.maple_thumbnail_url,
        "spacer_emoji": config.discord_spacer_emoji,
        "blocks": detail.blocks if detail else (),
    }
    if config.discord_bot_token and not config.test_mode:
        kwargs["bot_token"] = config.discord_bot_token
    message_ids = send_announcement(config.discord_webhook_url or "", item, **kwargs)
    return tuple(message_ids or ())


def _deletion_candidates(
    sent_ids: set[str], current_ids: set[str]
) -> list[str]:
    """Only inspect IDs still inside the official API's current history window."""
    current_numeric_ids = [
        int(announcement_id)
        for announcement_id in current_ids
        if announcement_id.isdecimal()
    ]
    if not current_numeric_ids:
        return []
    oldest_current_id = min(current_numeric_ids)
    return sorted(
        (
            announcement_id
            for announcement_id in sent_ids - current_ids
            if announcement_id.isdecimal()
            and int(announcement_id) >= oldest_current_id
        ),
        key=int,
    )


def _sync_deleted_announcements(
    config: Config,
    state: AnnouncementState,
    current_ids: set[str],
) -> bool:
    candidates = _deletion_candidates(state.sent_ids, current_ids)
    candidate_set = set(candidates)
    changed = False

    for announcement_id in list(state.missing_checks):
        if announcement_id not in candidate_set:
            state.missing_checks.pop(announcement_id, None)
            changed = True

    for announcement_id in candidates:
        checks = state.missing_checks.get(announcement_id, 0) + 1
        state.missing_checks[announcement_id] = checks
        changed = True
        if checks < 2:
            LOGGER.warning(
                "公告疑似已由官方刪除，等待下次成功檢查確認：ID=%s check=%d/2",
                announcement_id,
                checks,
            )
            continue

        message_ids = state.discord_message_ids.get(announcement_id, ())
        if message_ids:
            try:
                delete_announcement_messages(
                    config.discord_webhook_url or "",
                    message_ids,
                    timeout=config.request_timeout,
                    user_agent=config.user_agent,
                )
            except DiscordSendError as exc:
                LOGGER.warning(
                    "官方刪除公告同步至 Discord 失敗，將於下次重試："
                    "ID=%s message_count=%d reason=%s",
                    announcement_id,
                    len(message_ids),
                    exc,
                )
                continue
            LOGGER.info(
                "已同步刪除 Discord 公告：ID=%s message_count=%d",
                announcement_id,
                len(message_ids),
            )
        else:
            LOGGER.warning(
                "官方公告已確認刪除，但舊版 state 沒有 Discord message ID；"
                "僅移除狀態：ID=%s",
                announcement_id,
            )

        state.sent_ids.discard(announcement_id)
        state.discord_message_ids.pop(announcement_id, None)
        state.missing_checks.pop(announcement_id, None)

    return changed


def run(config: Config) -> int:
    announcements = fetch_announcements(
        timeout=config.request_timeout, user_agent=config.user_agent
    )
    current_ids = {item.announcement_id for item in announcements}
    state = load_state(config.state_file)

    if state is None:
        if config.require_existing_state:
            raise StateStoreError(
                "找不到既有狀態檔；為避免漏送，停止執行。"
                "請恢復狀態備份；首次部署才使用 initialize_state 建立基準。"
            )
        if not config.test_mode:
            save_state(
                config.state_file,
                AnnouncementState(sent_ids=set(current_ids)),
            )
            LOGGER.info("首次執行：已建立 %d 篇公告基準，本次不發送。", len(current_ids))
            return 0

        latest = announcements[0]
        LOGGER.info("TEST_MODE：發送目前最新公告 %s。", latest.announcement_id)
        _send(config, latest)
        save_state(
            config.state_file,
            AnnouncementState(sent_ids=set(current_ids)),
        )
        LOGGER.info("測試公告發送成功，已建立目前公告基準。")
        return 0

    if config.test_mode:
        latest = announcements[0]
        LOGGER.info("TEST_MODE：發送目前最新公告 %s（可重複測試）。", latest.announcement_id)
        _send(config, latest)
        LOGGER.info("測試公告發送成功；既有狀態不變。")
        return 0

    if _sync_deleted_announcements(config, state, current_ids):
        save_state(config.state_file, state)

    new_items = [
        item for item in announcements if item.announcement_id not in state.sent_ids
    ]
    if not new_items:
        LOGGER.info("沒有新公告。")
        return 0

    # API is newest-first; send oldest-first for a natural Discord timeline.
    first_item_error: AnnouncementDetailError | DiscordPayloadError | None = None
    failed_count = 0
    for item in reversed(new_items):
        LOGGER.info("正在發送公告 %s：%s", item.announcement_id, item.title)
        try:
            message_ids = _send(config, item)
        except (AnnouncementDetailError, DiscordPayloadError) as exc:
            # Keep failed IDs pending without starving independent announcements.
            failed_count += 1
            if first_item_error is None:
                first_item_error = exc
            LOGGER.warning(
                "公告處理失敗，保留待重試並繼續下一篇：ID=%s reason=%s",
                item.announcement_id, exc,
            )
            continue
        state.sent_ids.add(item.announcement_id)
        if message_ids:
            state.discord_message_ids[item.announcement_id] = message_ids
        state.missing_checks.pop(item.announcement_id, None)
        save_state(config.state_file, state)
        LOGGER.info("公告 %s 發送成功並已記錄。", item.announcement_id)
    if first_item_error is not None:
        LOGGER.error("本次有 %d 篇公告待重試；其餘成功公告已保存。", failed_count)
        raise first_item_error
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        return run(Config.from_env())
    except (
        ValueError,
        AnnouncementDetailError,
        MapleParserError,
        DiscordSendError,
        StateStoreError,
    ) as exc:
        LOGGER.error("%s", exc)
        return 1
    except Exception:
        LOGGER.exception("發生未預期錯誤；狀態檔未被主動更新。")
        return 1


if __name__ == "__main__":
    sys.exit(main())
