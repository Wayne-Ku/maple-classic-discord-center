from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

import requests

import app
from announcement_detail import AnnouncementDetail, AnnouncementDetailError, ImageBlock, TextBlock
from config import Config
from discord_sender import DiscordSendError, send_announcement as build_and_send_announcement
from maple_parser import Announcement, MapleParserError
from tools import backfill_announcements as backfill


def item(identifier: str, when: str) -> Announcement:
    return Announcement(identifier, "活動", f"公告 {identifier}", when, f"https://example.com/{identifier}")


def config() -> Config:
    return Config("https://discord.com/api/webhooks/1/token", False, Path("unused-state.json"), 5, "test")


def test_select_announcements_sorts_by_date_then_numeric_id():
    selected = backfill.select_announcements(
        [item("10", "2026/07/02"), item("3", "2026/07/01"), item("2", "2026/07/01")],
        limit=None,
        since=None,
        reverse=False,
    )
    assert [announcement.announcement_id for announcement in selected] == ["2", "3", "10"]


def test_select_announcements_supports_limit_since_and_reverse():
    announcements = [item("1", "2026/07/01"), item("2", "2026/07/02"), item("3", "2026/07/03")]

    assert [entry.announcement_id for entry in backfill.select_announcements(announcements, limit=1, since=None, reverse=False)] == ["1"]
    assert [entry.announcement_id for entry in backfill.select_announcements(announcements, limit=None, since=date(2026, 7, 2), reverse=False)] == ["2", "3"]
    assert [entry.announcement_id for entry in backfill.select_announcements(announcements, limit=None, since=None, reverse=True)] == ["3", "2", "1"]


def test_detail_failure_skips_and_continues_to_later_success(monkeypatch):
    announcements = [item("1", "2026/07/01"), item("2", "2026/07/02")]
    monkeypatch.setattr(backfill, "fetch_announcements", lambda **kwargs: announcements)

    def detail(entry, **kwargs):
        if entry.announcement_id == "1":
            raise AnnouncementDetailError("no body")
        return SimpleNamespace(plain_text="正文", images=())

    sent, sleeps, output = [], [], []
    monkeypatch.setattr(backfill, "fetch_announcement_detail", detail)
    monkeypatch.setattr(backfill, "send_announcement", lambda _url, entry, **_kwargs: sent.append(entry.announcement_id))

    summary = backfill.run_backfill(config(), sleep=sleeps.append, output=output.append)

    assert sent == ["2"]
    assert summary == backfill.BackfillSummary(success=1, skipped=1, failed=0, total=2)
    assert summary.success + summary.skipped + summary.failed == summary.total
    assert sleeps == [1]
    assert "跳過：官方公告沒有可用正文。" in output


def test_discord_failure_continues_to_later_announcements(monkeypatch):
    announcements = [item("1", "2026/07/01"), item("2", "2026/07/02"), item("3", "2026/07/03")]
    monkeypatch.setattr(backfill, "fetch_announcements", lambda **kwargs: announcements)
    monkeypatch.setattr(backfill, "fetch_announcement_detail", lambda _entry, **_kwargs: SimpleNamespace(plain_text="正文", images=()))
    sent, sleeps, output = [], [], []

    def send(_url, entry, **_kwargs):
        if entry.announcement_id == "2":
            raise DiscordSendError("webhook secret must not be printed")
        sent.append(entry.announcement_id)

    monkeypatch.setattr(backfill, "send_announcement", send)
    summary = backfill.run_backfill(config(), sleep=sleeps.append, output=output.append)

    assert sent == ["1", "3"]
    assert summary == backfill.BackfillSummary(success=2, skipped=0, failed=1, total=3)
    assert sleeps == [1, 1]
    assert "失敗：Discord 發送失敗。" in output
    assert all("secret" not in line for line in output)


def test_all_skipped_completes_with_zero_exit_code(monkeypatch):
    monkeypatch.setattr(backfill, "Config", SimpleNamespace(from_env=lambda: config()))
    monkeypatch.setattr(backfill, "fetch_announcements", lambda **_kwargs: [item("1", "2026/07/01")])
    monkeypatch.setattr(backfill, "fetch_announcement_detail", lambda _entry, **_kwargs: (_ for _ in ()).throw(AnnouncementDetailError("no body")))

    assert backfill.main([]) == 0


def test_list_fetch_failure_returns_one(monkeypatch):
    monkeypatch.setattr(backfill, "Config", SimpleNamespace(from_env=lambda: config()))
    monkeypatch.setattr(backfill, "fetch_announcements", lambda **_kwargs: (_ for _ in ()).throw(MapleParserError("list failed")))

    assert backfill.main([]) == 1


def test_request_failure_is_counted_and_state_store_is_not_used(monkeypatch):
    monkeypatch.setattr(backfill, "fetch_announcements", lambda **_kwargs: [item("1", "2026/07/01")])
    monkeypatch.setattr(backfill, "fetch_announcement_detail", lambda _entry, **_kwargs: (_ for _ in ()).throw(requests.Timeout("timeout")))
    output = []

    summary = backfill.run_backfill(config(), sleep=lambda _seconds: None, output=output.append)

    assert summary == backfill.BackfillSummary(success=0, skipped=0, failed=1, total=1)
    assert "失敗：Timeout" in output
    assert "state_store" not in vars(backfill)


def test_id_mode_sends_only_the_requested_announcement_and_ignores_filters(monkeypatch):
    announcements = [item("1", "2026/07/01"), item("2", "2026/07/02"), item("3", "2026/07/03")]
    monkeypatch.setattr(backfill, "fetch_announcements", lambda **_kwargs: announcements)
    ordered_blocks = (
        TextBlock("body 2 with [link](https://example.com/event)"),
        ImageBlock("https://cdn.example.com/guide.jpg"),
        TextBlock("closing text"),
    )
    monkeypatch.setattr(
        backfill,
        "fetch_announcement_detail",
        lambda entry, **_kwargs: SimpleNamespace(
            plain_text=f"body {entry.announcement_id}",
            images=("https://cdn.example.com/guide.jpg",),
            blocks=ordered_blocks,
        ),
    )
    sent = []
    monkeypatch.setattr(
        backfill,
        "send_announcement",
        lambda _url, entry, **kwargs: sent.append((entry.announcement_id, kwargs)),
    )

    summary = backfill.run_backfill(
        config(),
        announcement_id="2",
        limit=1,
        since=date(2030, 1, 1),
        reverse=True,
        sleep=lambda _seconds: None,
        output=lambda _line: None,
    )

    assert summary == backfill.BackfillSummary(success=1, skipped=0, failed=0, total=1)
    assert sent == [
        (
            "2",
            {
                "timeout": 5,
                "user_agent": "test",
                "thumbnail_url": None,
                "blocks": ordered_blocks,
                "history_mode": True,
            },
        )
    ]


def test_id_mode_rejects_an_unknown_announcement(monkeypatch):
    monkeypatch.setattr(backfill, "fetch_announcements", lambda **_kwargs: [item("1", "2026/07/01")])

    with pytest.raises(ValueError, match="找不到公告 ID：999"):
        backfill.run_backfill(config(), announcement_id="999", output=lambda _line: None)


def test_parse_args_accepts_id_with_ignored_filters():
    args = backfill._parse_args(["--id", "82221", "--limit", "1", "--since", "2030-01-01", "--reverse"])

    assert args.announcement_id == "82221"
    assert args.limit == 1
    assert args.since == date(2030, 1, 1)
    assert args.reverse is True


def test_backfill_and_normal_flow_build_the_same_ordered_embed_structure(monkeypatch):
    target = item("82176", "2026/07/22")
    blocks = (
        TextBlock("body\nsetup guide:"),
        ImageBlock("https://cdn.example.com/one.jpg"),
        ImageBlock("https://cdn.example.com/two.jpg"),
        TextBlock("short closing"),
    )
    detail = AnnouncementDetail(
        plain_text="body\nsetup guide:\nshort closing",
        images=(
            "https://cdn.example.com/one.jpg",
            "https://cdn.example.com/two.jpg",
        ),
        blocks=blocks,
    )
    payloads = []

    class SuccessfulResponse:
        status_code = 204
        text = ""

    class PayloadSession:
        def post(self, _url, **kwargs):
            payloads.append(kwargs["json"])
            return SuccessfulResponse()

        def close(self):
            return None

    def capture_payload(url, announcement, **kwargs):
        build_and_send_announcement(
            url,
            announcement,
            session=PayloadSession(),
            **kwargs,
        )

    monkeypatch.setattr(app, "fetch_announcement_detail", lambda *_args, **_kwargs: detail)
    monkeypatch.setattr(app, "send_announcement", capture_payload)
    app._send(config(), target)

    monkeypatch.setattr(backfill, "fetch_announcements", lambda **_kwargs: [target])
    monkeypatch.setattr(
        backfill,
        "fetch_announcement_detail",
        lambda *_args, **_kwargs: detail,
    )
    monkeypatch.setattr(backfill, "send_announcement", capture_payload)
    backfill.run_backfill(
        config(),
        announcement_id="82176",
        sleep=lambda _seconds: None,
        output=lambda _line: None,
    )

    def embed_structure(payload):
        return [
            ("image", embed["image"]["url"])
            if "image" in embed
            else ("text", embed["description"])
            for embed in payload["embeds"]
        ]

    assert embed_structure(payloads[0]) == embed_structure(payloads[1])
    assert [
        index for index, embed in enumerate(payloads[0]["embeds"]) if "footer" in embed
    ] == [3]
    assert [
        index for index, embed in enumerate(payloads[1]["embeds"]) if "footer" in embed
    ] == [3]
