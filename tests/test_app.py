import logging
from pathlib import Path

import app
import pytest
from announcement_detail import AnnouncementDetail, TextBlock
from config import Config
from maple_parser import Announcement
from state_store import load_sent_ids


@pytest.fixture(autouse=True)
def mock_announcement_detail(monkeypatch):
    monkeypatch.setattr(
        app,
        "fetch_announcement_detail",
        lambda *_args, **_kwargs: AnnouncementDetail("正文", images=()),
    )


def make_config(path: Path, test_mode: bool = False) -> Config:
    return Config("https://discord.invalid/webhook", test_mode, path, 5, "test")


def make_config_with_thumbnail(
    path: Path,
    test_mode: bool = False,
    thumbnail_url: str | None = None,
    spacer_emoji: str | None = None,
) -> Config:
    return Config(
        "https://discord.invalid/webhook",
        test_mode,
        path,
        5,
        "test",
        thumbnail_url,
        spacer_emoji,
    )


def test_first_normal_run_only_creates_baseline(monkeypatch, tmp_path):
    items = [Announcement("2", "活動", "新", "2026/02/02", "https://x/2")]
    monkeypatch.setattr(app, "fetch_announcements", lambda **kwargs: items)
    monkeypatch.setattr(
        app, "send_announcement", lambda *args, **kwargs: pytest.fail("must not send")
    )
    path = tmp_path / "state.json"
    assert app.run(make_config(path)) == 0
    assert load_sent_ids(path) == {"2"}


def test_failed_send_is_not_recorded(monkeypatch, tmp_path):
    path = tmp_path / "state.json"
    path.write_text('{"version": 1, "sent_announcement_ids": ["1"]}', encoding="utf-8")
    items = [
        Announcement("2", "活動", "新", "2026/02/02", "https://x/2"),
        Announcement("1", "活動", "舊", "2026/02/01", "https://x/1"),
    ]
    monkeypatch.setattr(app, "fetch_announcements", lambda **kwargs: items)

    def fail(*args, **kwargs):
        raise app.DiscordSendError("failed")

    monkeypatch.setattr(app, "send_announcement", fail)
    with pytest.raises(app.DiscordSendError):
        app.run(make_config(path))
    assert load_sent_ids(path) == {"1"}


def test_test_mode_sends_latest_without_changing_existing_state(monkeypatch, tmp_path):
    path = tmp_path / "state.json"
    path.write_text('{"version": 1, "sent_announcement_ids": ["1"]}', encoding="utf-8")
    items = [Announcement("2", "重要", "最新", "2026/02/02", "https://x/2")]
    sent = []
    monkeypatch.setattr(app, "fetch_announcements", lambda **kwargs: items)
    monkeypatch.setattr(
        app, "send_announcement", lambda _url, item, **kwargs: sent.append(item)
    )
    assert app.run(make_config(path, test_mode=True)) == 0
    assert [item.announcement_id for item in sent] == ["2"]
    assert load_sent_ids(path) == {"1"}


def test_first_test_mode_sends_latest_and_creates_baseline(monkeypatch, tmp_path):
    path = tmp_path / "state.json"
    items = [
        Announcement("2", "重要", "最新", "2026/02/02", "https://x/2"),
        Announcement("1", "活動", "較舊", "2026/02/01", "https://x/1"),
    ]
    sent = []
    monkeypatch.setattr(app, "fetch_announcements", lambda **kwargs: items)
    monkeypatch.setattr(app, "send_announcement", lambda _url, item, **kwargs: sent.append(item))

    assert app.run(make_config(path, test_mode=True)) == 0
    assert [item.announcement_id for item in sent] == ["2"]
    assert load_sent_ids(path) == {"1", "2"}


def test_test_mode_passes_thumbnail_url(monkeypatch, tmp_path):
    path = tmp_path / "state.json"
    items = [Announcement("2", "更新", "測試公告", "2026/02/02", "https://x/2")]
    calls = []
    monkeypatch.setattr(app, "fetch_announcements", lambda **kwargs: items)
    monkeypatch.setattr(
        app, "send_announcement", lambda _url, _item, **kwargs: calls.append(kwargs)
    )

    assert app.run(
        make_config_with_thumbnail(path, test_mode=True, thumbnail_url="https://cdn.example.com/logo")
    ) == 0
    assert calls == [
        {
            "timeout": 5,
            "user_agent": "test",
            "thumbnail_url": "https://cdn.example.com/logo",
            "spacer_emoji": None,
            "blocks": (TextBlock("正文"),),
        }
    ]


def test_normal_mode_passes_none_thumbnail_url(monkeypatch, tmp_path):
    path = tmp_path / "state.json"
    path.write_text('{"version": 1, "sent_announcement_ids": ["1"]}', encoding="utf-8")
    items = [Announcement("2", "更新", "測試公告", "2026/02/02", "https://x/2")]
    calls = []
    monkeypatch.setattr(app, "fetch_announcements", lambda **kwargs: items)
    monkeypatch.setattr(
        app, "send_announcement", lambda _url, _item, **kwargs: calls.append(kwargs)
    )

    assert app.run(make_config_with_thumbnail(path)) == 0
    assert calls == [
        {
            "timeout": 5,
            "user_agent": "test",
            "thumbnail_url": None,
            "spacer_emoji": None,
            "blocks": (TextBlock("正文"),),
        }
    ]


def test_test_mode_passes_spacer_emoji(monkeypatch, tmp_path):
    path = tmp_path / "state.json"
    emoji = "<:blank:123456789012345678>"
    items = [Announcement("2", "更新", "測試公告", "2026/02/02", "https://x/2")]
    calls = []
    monkeypatch.setattr(app, "fetch_announcements", lambda **kwargs: items)
    monkeypatch.setattr(
        app, "send_announcement", lambda _url, _item, **kwargs: calls.append(kwargs)
    )

    assert app.run(
        make_config_with_thumbnail(
            path,
            test_mode=True,
            spacer_emoji=emoji,
        )
    ) == 0
    assert calls[0]["spacer_emoji"] == emoji


def test_normal_mode_passes_spacer_emoji(monkeypatch, tmp_path):
    path = tmp_path / "state.json"
    path.write_text(
        '{"version": 1, "sent_announcement_ids": ["1"]}',
        encoding="utf-8",
    )
    emoji = "<:blank:123456789012345678>"
    items = [Announcement("2", "更新", "測試公告", "2026/02/02", "https://x/2")]
    calls = []
    monkeypatch.setattr(app, "fetch_announcements", lambda **kwargs: items)
    monkeypatch.setattr(
        app, "send_announcement", lambda _url, _item, **kwargs: calls.append(kwargs)
    )

    assert app.run(
        make_config_with_thumbnail(path, spacer_emoji=emoji)
    ) == 0
    assert calls[0]["spacer_emoji"] == emoji


def test_detail_failure_does_not_send_or_update_state(monkeypatch, tmp_path, caplog):
    path = tmp_path / "state.json"
    path.write_text(
        '{"version": 1, "sent_announcement_ids": ["1"]}',
        encoding="utf-8",
    )
    items = [Announcement("2", "活動", "新公告", "2026/02/02", "https://x/2")]
    monkeypatch.setattr(app, "fetch_announcements", lambda **kwargs: items)
    monkeypatch.setattr(
        app,
        "fetch_announcement_detail",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            app.AnnouncementDetailError("missing")
        ),
    )
    monkeypatch.setattr(
        app,
        "send_announcement",
        lambda *_args, **_kwargs: pytest.fail("must not send"),
    )

    with caplog.at_level(logging.WARNING):
        with pytest.raises(app.AnnouncementDetailError, match="missing"):
            app.run(make_config(path))

    assert load_sent_ids(path) == {"1"}
    assert "ID=2" in caplog.text
    assert "title=新公告" in caplog.text
    assert "reason=missing" in caplog.text


def test_each_new_announcement_fetches_detail_once(monkeypatch, tmp_path):
    path = tmp_path / "state.json"
    path.write_text(
        '{"version": 1, "sent_announcement_ids": ["1"]}',
        encoding="utf-8",
    )
    items = [
        Announcement("3", "活動", "最新", "2026/02/03", "https://x/3"),
        Announcement("2", "活動", "較舊", "2026/02/02", "https://x/2"),
    ]
    fetched = []
    monkeypatch.setattr(app, "fetch_announcements", lambda **kwargs: items)

    def fetch_detail(item, **_kwargs):
        fetched.append(item.announcement_id)
        return AnnouncementDetail(f"正文 {item.announcement_id}")

    monkeypatch.setattr(app, "fetch_announcement_detail", fetch_detail)
    monkeypatch.setattr(app, "send_announcement", lambda *_args, **_kwargs: None)

    assert app.run(make_config(path)) == 0
    assert fetched == ["2", "3"]


def test_no_new_announcements_does_not_send_or_write(monkeypatch, tmp_path):
    path = tmp_path / "state.json"
    path.write_text('{"version": 1, "sent_announcement_ids": ["1"]}', encoding="utf-8")
    monkeypatch.setattr(
        app,
        "fetch_announcements",
        lambda **kwargs: [Announcement("1", "活動", "既有", "2026/02/01", "https://x/1")],
    )
    monkeypatch.setattr(app, "send_announcement", lambda *args, **kwargs: pytest.fail("must not send"))
    monkeypatch.setattr(app, "save_sent_ids", lambda *args, **kwargs: pytest.fail("must not write"))

    assert app.run(make_config(path)) == 0


def test_multiple_new_announcements_send_oldest_first(monkeypatch, tmp_path):
    path = tmp_path / "state.json"
    path.write_text('{"version": 1, "sent_announcement_ids": ["1"]}', encoding="utf-8")
    items = [
        Announcement("3", "活動", "最新", "2026/02/03", "https://x/3"),
        Announcement("2", "活動", "中間", "2026/02/02", "https://x/2"),
        Announcement("1", "活動", "既有", "2026/02/01", "https://x/1"),
    ]
    sent = []
    monkeypatch.setattr(app, "fetch_announcements", lambda **kwargs: items)
    monkeypatch.setattr(app, "send_announcement", lambda _url, item, **kwargs: sent.append(item))

    assert app.run(make_config(path)) == 0
    assert [item.announcement_id for item in sent] == ["2", "3"]
    assert load_sent_ids(path) == {"1", "2", "3"}


def test_second_send_failure_keeps_first_successful_state(monkeypatch, tmp_path):
    path = tmp_path / "state.json"
    path.write_text('{"version": 1, "sent_announcement_ids": ["1"]}', encoding="utf-8")
    items = [
        Announcement("3", "活動", "最新", "2026/02/03", "https://x/3"),
        Announcement("2", "活動", "中間", "2026/02/02", "https://x/2"),
    ]
    calls = []
    monkeypatch.setattr(app, "fetch_announcements", lambda **kwargs: items)

    def send(_url, item, **kwargs):
        calls.append(item.announcement_id)
        if item.announcement_id == "3":
            raise app.DiscordSendError("second failed")

    monkeypatch.setattr(app, "send_announcement", send)
    with pytest.raises(app.DiscordSendError):
        app.run(make_config(path))
    assert calls == ["2", "3"]
    assert load_sent_ids(path) == {"1", "2"}


def test_82279_partial_chunk_failure_is_not_recorded(monkeypatch, tmp_path):
    path = tmp_path / "state.json"
    path.write_text(
        '{"version": 1, "sent_announcement_ids": ["82278"]}',
        encoding="utf-8",
    )
    item = Announcement(
        "82279",
        "重要",
        "新楓之谷：經典版《0802(日)遊戲異常行為制裁公告》",
        "2026/08/02",
        "https://x/82279",
    )
    monkeypatch.setattr(app, "fetch_announcements", lambda **_kwargs: [item])
    monkeypatch.setattr(
        app,
        "send_announcement",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            app.DiscordSendError("chunk=2/8 failed")
        ),
    )

    with pytest.raises(app.DiscordSendError, match="chunk=2/8"):
        app.run(make_config(path))

    assert load_sent_ids(path) == {"82278"}


def test_82279_is_recorded_only_after_complete_send_and_not_sent_again(
    monkeypatch, tmp_path
):
    path = tmp_path / "state.json"
    path.write_text(
        '{"version": 1, "sent_announcement_ids": ["82278"]}',
        encoding="utf-8",
    )
    item = Announcement(
        "82279",
        "重要",
        "新楓之谷：經典版《0802(日)遊戲異常行為制裁公告》",
        "2026/08/02",
        "https://x/82279",
    )
    calls = []
    monkeypatch.setattr(app, "fetch_announcements", lambda **_kwargs: [item])
    monkeypatch.setattr(
        app,
        "send_announcement",
        lambda _url, announcement, **_kwargs: calls.append(
            announcement.announcement_id
        ),
    )

    assert app.run(make_config(path)) == 0
    assert load_sent_ids(path) == {"82278", "82279"}
    assert app.run(make_config(path)) == 0
    assert calls == ["82279"]


def test_normal_mode_passes_bot_token_for_auto_publish(monkeypatch, tmp_path):
    path = tmp_path / "state.json"
    path.write_text(
        '{"version": 1, "sent_announcement_ids": ["1"]}',
        encoding="utf-8",
    )
    items = [Announcement("2", "更新", "測試公告", "2026/02/02", "https://x/2")]
    calls = []
    monkeypatch.setattr(app, "fetch_announcements", lambda **kwargs: items)
    monkeypatch.setattr(
        app, "send_announcement", lambda _url, _item, **kwargs: calls.append(kwargs)
    )
    config = Config(
        "https://discord.invalid/webhook",
        False,
        path,
        5,
        "test",
        discord_bot_token="test-bot-token",
    )

    assert app.run(config) == 0
    assert calls[0]["bot_token"] == "test-bot-token"


def test_test_mode_does_not_publish_to_following_servers(monkeypatch, tmp_path):
    path = tmp_path / "state.json"
    path.write_text(
        '{"version": 1, "sent_announcement_ids": ["1"]}',
        encoding="utf-8",
    )
    items = [Announcement("2", "更新", "測試公告", "2026/02/02", "https://x/2")]
    calls = []
    monkeypatch.setattr(app, "fetch_announcements", lambda **kwargs: items)
    monkeypatch.setattr(
        app, "send_announcement", lambda _url, _item, **kwargs: calls.append(kwargs)
    )
    config = Config(
        "https://discord.invalid/webhook",
        True,
        path,
        5,
        "test",
        discord_bot_token="test-bot-token",
    )

    assert app.run(config) == 0
    assert "bot_token" not in calls[0]
