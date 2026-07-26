from pathlib import Path

import app
import pytest
from config import Config
from maple_parser import Announcement
from state_store import load_sent_ids


def make_config(path: Path, test_mode: bool = False) -> Config:
    return Config("https://discord.invalid/webhook", test_mode, path, 5, "test")


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
