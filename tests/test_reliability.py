from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

import app
import discord_sender as sender
import state_store
from announcement_detail import AnnouncementDetail, AnnouncementDetailError
from config import Config
from maple_parser import Announcement
from state_store import AnnouncementState, load_state, save_state

WEBHOOK = "https://discord.com/api/webhooks/123456/test-token"


def item(identifier):
    return Announcement(identifier, "重要", "公告", "2026/09/23", f"https://example.com/{identifier}")


def config(path):
    return Config(WEBHOOK, False, path, 5, "test")


@pytest.mark.parametrize("error_type", [AnnouncementDetailError, sender.DiscordPayloadError])
def test_failed_item_does_not_block_newer_items_and_is_retried(error_type, tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    save_state(path, AnnouncementState({"1"}, {"1": ("101",)}))
    monkeypatch.setattr(app, "fetch_announcements", lambda **kw: [item("3"), item("2"), item("1")])
    attempts = []
    fail = True

    def send(_config, announcement):
        attempts.append(announcement.announcement_id)
        if fail and announcement.announcement_id == "2":
            raise error_type("temporary invalid announcement")
        return ("10" + announcement.announcement_id,)

    monkeypatch.setattr(app, "_send", send)
    with pytest.raises(error_type):
        app.run(config(path))
    state = load_state(path)
    assert state.sent_ids == {"1", "3"}
    assert state.discord_message_ids == {"1": ("101",), "3": ("103",)}
    fail = False
    assert app.run(config(path)) == 0
    assert attempts == ["2", "3", "2"]
    assert load_state(path).discord_message_ids == {"1": ("101",), "2": ("102",), "3": ("103",)}


def test_shared_discord_outage_stops_the_batch(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    save_state(path, AnnouncementState({"1"}))
    monkeypatch.setattr(app, "fetch_announcements", lambda **kw: [item("3"), item("2"), item("1")])
    attempts = []

    def send(_config, announcement):
        attempts.append(announcement.announcement_id)
        raise sender.DiscordSendError("outage")

    monkeypatch.setattr(app, "_send", send)
    with pytest.raises(sender.DiscordSendError):
        app.run(config(path))
    assert attempts == ["2"]
    assert load_state(path).sent_ids == {"1"}


@pytest.mark.parametrize("test_mode", [False, True])
def test_required_missing_state_cannot_send_or_reinitialize(test_mode, tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    monkeypatch.setattr(app, "fetch_announcements", lambda **kw: [item("2")])
    monkeypatch.setattr(app, "_send", lambda *args: pytest.fail("must not send"))
    with pytest.raises(state_store.StateStoreError, match="找不到既有狀態檔"):
        app.run(replace(config(path), test_mode=test_mode, require_existing_state=True))
    assert not path.exists()


def test_existing_v2_state_and_deletion_metadata_are_preserved(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    before = AnnouncementState({"1", "2"}, {"1": ("101", "102"), "2": ("201",)}, {"2": 1})
    save_state(path, before)
    monkeypatch.setattr(app, "fetch_announcements", lambda **kw: [item("3"), item("2"), item("1")])
    monkeypatch.setattr(app, "_send", lambda *_: ("301", "302"))
    assert app.run(replace(config(path), require_existing_state=True)) == 0
    after = load_state(path)
    assert after.sent_ids == {"1", "2", "3"}
    assert after.discord_message_ids == {**before.discord_message_ids, "3": ("301", "302")}
    assert after.missing_checks == {}


def test_fsync_failure_keeps_v2_state_and_cleans_temp(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    before = AnnouncementState({"1"}, {"1": ("101", "102")}, {"1": 1})
    save_state(path, before)
    original = path.read_bytes()

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(state_store.os, "fsync", fail)
    with pytest.raises(state_store.StateStoreError):
        save_state(path, AnnouncementState({"1", "2"}))
    assert path.read_bytes() == original
    assert not list(tmp_path.glob(".state.json.*.tmp"))


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "0", "-1"])
def test_timeout_must_be_positive_finite(value, monkeypatch):
    monkeypatch.setenv("REQUEST_TIMEOUT", value)
    with pytest.raises(ValueError):
        Config.from_env()


class Response:
    text = ""

    def __init__(self, status, body):
        self.status_code = status
        self.body = body

    def json(self):
        return self.body


class Session:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.response


@pytest.mark.parametrize("status, body", [
    (204, {}), (200, {}), (200, {"id": "abc"}), (200, {"id": 123}),
    (200, []), (200, {"id": None}), (201, {"id": "123"}),
])
def test_no_completion_without_valid_message_confirmation(status, body):
    session = Session(Response(status, body))
    with pytest.raises(sender.DiscordSendError, match="訊息 ID"):
        sender.send_announcement(WEBHOOK, item("2"), content="正文", user_agent="test", session=session)
    assert len(session.calls) == 1


def test_http_400_preserves_item_error_type_through_chunk_wrapper():
    session = Session(Response(400, {}))
    with pytest.raises(sender.DiscordPayloadError):
        sender.send_announcement(WEBHOOK, item("2"), content="正文", user_agent="test", session=session)


def test_long_rate_limit_never_retries_early():
    session = Session(Response(429, {"retry_after": 120}))
    sleeps = []
    with pytest.raises(sender.DiscordSendError, match="限流"):
        sender.send_announcement(WEBHOOK, item("2"), content="正文", user_agent="test", session=session, sleep=sleeps.append)
    assert len(session.calls) == 1
    assert sleeps == []


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, "invalid"])
def test_invalid_retry_after_uses_fallback(value):
    assert sender._retry_after(Response(429, {"retry_after": value}), 2) == 2


def test_no_state_update_if_discord_confirmation_is_missing(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    before = AnnouncementState({"1"}, {"1": ("101",)})
    save_state(path, before)
    monkeypatch.setattr(app, "fetch_announcements", lambda **kw: [item("2"), item("1")])
    monkeypatch.setattr(app, "fetch_announcement_detail", lambda *args, **kw: AnnouncementDetail("正文"))
    session = Session(Response(204, {}))
    session.close = lambda: None
    monkeypatch.setattr(sender.requests, "Session", lambda: session)
    with pytest.raises(sender.DiscordSendError):
        app.run(config(path))
    assert load_state(path) == before


def test_wait_false_cannot_override_delivery_confirmation():
    session = Session(Response(200, {"id": "1234567890"}))
    sender.send_announcement(
        WEBHOOK + "?wait=false&thread_id=456", item("2"),
        content="正文", user_agent="test", session=session,
    )
    args, kwargs = session.calls[0]
    assert parse_qs(urlsplit(args[0]).query) == {"thread_id": ["456"]}
    assert kwargs["params"] == {"wait": "true"}


def test_83754_empty_primary_api_uses_legacy_news_id_not_bulletin_id():
    from announcement_detail import fetch_announcement_detail, LEGACY_NEWS_API_URL

    class DetailSession:
        def __init__(self):
            self.calls = []

        def post(self, url, **kwargs):
            self.calls.append((url, kwargs))
            response = Response(200, (
                {"code": 1, "data": {"myDataSet": {"table": {"content": None}}}}
                if len(self.calls) == 1
                else {"ResultCode": 1, "ResultData": {"Contents": "<p>驗證碼安全提醒</p>"}}
            ))
            response.raise_for_status = lambda: None
            return response

        def get(self, *args, **kwargs):
            pytest.fail("A valid legacy response must not fall back to a template page")

    session = DetailSession()
    announcement = replace(
        item("83754"),
        url="https://tw.beanfun.com/news/content.aspx?p=1&news_id=6063",
    )
    detail = fetch_announcement_detail(announcement, timeout=1, user_agent="test", session=session)
    assert detail.plain_text == "驗證碼安全提醒"
    assert session.calls[1][0] == LEGACY_NEWS_API_URL
    assert session.calls[1][1]["data"] == {"NewsID": "6063", "ServiceDataID": "0"}


def test_workflow_requires_state_unless_explicitly_initialized():
    text = (Path(__file__).parents[1] / ".github/workflows/check-announcements.yml").read_text(encoding="utf-8")
    assert "initialize_state:" in text
    assert "REQUIRE_EXISTING_STATE:" in text
    assert "inputs.initialize_state && 'false' || 'true'" in text
    assert "load_state(Path('data/state.json'))" in text
