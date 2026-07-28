"""Backfill historical Maple Classic announcements to Discord."""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from announcement_detail import AnnouncementDetailError, fetch_announcement_detail
from config import Config
from discord_sender import DiscordSendError, send_announcement
from maple_parser import Announcement, MapleParserError, fetch_announcements


@dataclass(frozen=True)
class BackfillSummary:
    success: int
    skipped: int
    failed: int
    total: int


def _announcement_date(value: str) -> date:
    normalized = value.strip()[:10].replace("/", "-")
    return datetime.strptime(normalized, "%Y-%m-%d").date()


def _announcement_id_sort_key(value: str) -> tuple[int, int | str]:
    return (0, int(value)) if value.isdigit() else (1, value)


def select_announcements(
    announcements: Sequence[Announcement], *, limit: int | None, since: date | None, reverse: bool
) -> list[Announcement]:
    selected = [item for item in announcements if since is None or _announcement_date(item.date) >= since]
    selected.sort(
        key=lambda item: (_announcement_date(item.date), _announcement_id_sort_key(item.announcement_id)),
        reverse=reverse,
    )
    return selected[:limit] if limit is not None else selected


def _print_summary(summary: BackfillSummary, output: Callable[[str], None]) -> None:
    output("回填完成。")
    output(f"成功：{summary.success}")
    output(f"跳過：{summary.skipped}")
    output(f"失敗：{summary.failed}")
    output(f"總計：{summary.total}")


def run_backfill(
    config: Config,
    *,
    limit: int | None = None,
    since: date | None = None,
    reverse: bool = False,
    announcement_id: str | None = None,
    sleep: Callable[[float], None] = time.sleep,
    output: Callable[[str], None] = print,
) -> BackfillSummary:
    fetched_announcements = fetch_announcements(
        timeout=config.request_timeout, user_agent=config.user_agent
    )
    if announcement_id is not None:
        announcements = [
            item for item in fetched_announcements if item.announcement_id == announcement_id
        ]
        if not announcements:
            raise ValueError(f"找不到公告 ID：{announcement_id}")
    else:
        announcements = select_announcements(
            fetched_announcements,
            limit=limit,
            since=since,
            reverse=reverse,
        )
    output(f"共找到 {len(announcements)} 篇公告。")
    output("開始回填...")
    success = skipped = failed = 0

    for index, item in enumerate(announcements, start=1):
        output(f"[{index}/{len(announcements)}]")
        output(item.date)
        output(item.title)
        try:
            detail = fetch_announcement_detail(
                item, timeout=config.request_timeout, user_agent=config.user_agent
            )
            send_kwargs = {
                "timeout": config.request_timeout,
                "user_agent": config.user_agent,
                "thumbnail_url": config.maple_thumbnail_url,
                "history_mode": True,
            }
            blocks = getattr(detail, "blocks", None)
            if blocks:
                send_kwargs["blocks"] = blocks
            else:
                send_kwargs["content"] = detail.plain_text
                send_kwargs["images"] = getattr(detail, "images", ())
            send_announcement(config.discord_webhook_url or "", item, **send_kwargs)
        except AnnouncementDetailError:
            skipped += 1
            output("跳過：官方公告沒有可用正文。")
            continue
        except DiscordSendError:
            failed += 1
            output("失敗：Discord 發送失敗。")
            continue
        except requests.RequestException as exc:
            failed += 1
            output(f"失敗：{type(exc).__name__}")
            continue
        except Exception as exc:
            failed += 1
            output(f"失敗：{type(exc).__name__}")
            continue

        success += 1
        output("成功")
        sleep(1)

    summary = BackfillSummary(success=success, skipped=skipped, failed=failed, total=len(announcements))
    _print_summary(summary, output)
    return summary


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="將官方歷史公告回填至 Discord。")
    parser.add_argument("--limit", type=int, help="只回填排序後的前 N 篇公告。")
    parser.add_argument("--since", type=date.fromisoformat, help="只回填此日期（含）以後的公告。")
    parser.add_argument("--reverse", action="store_true", help="依新到舊順序回填。")
    parser.add_argument("--id", dest="announcement_id", help="只發送指定公告 ID；忽略 --since、--limit 與 --reverse。")
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit 必須為正整數。")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code == 0 else 1

    try:
        config = Config.from_env()
        if not config.discord_webhook_url:
            raise ValueError("DISCORD_WEBHOOK_URL 未設定。")
        run_backfill(
            config,
            limit=args.limit,
            since=args.since,
            reverse=args.reverse,
            announcement_id=args.announcement_id,
        )
    except (ValueError, MapleParserError, requests.RequestException) as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
