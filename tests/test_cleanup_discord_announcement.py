from pathlib import Path

import pytest

from config import Config
from tools import cleanup_discord_announcement as cleanup


class Response:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class Session:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.get_calls = []
        self.delete_calls = []
        self.closed = False

    def get(self, *args, **kwargs):
        self.get_calls.append((args, kwargs))
        return next(self.responses)

    def delete(self, url, **kwargs):
        self.delete_calls.append((url, kwargs))
        return Response(None, 204)

    def close(self):
        self.closed = True


def config():
    return Config(
        "https://discord.com/api/webhooks/123/token",
        False,
        Path("unused.json"),
        5,
        "test-agent",
        discord_bot_token="bot-token",
    )


def message(message_id, announcement_id, webhook_id="123"):
    return {
        "id": message_id,
        "webhook_id": webhook_id,
        "embeds": [
            {
                "footer": {
                    "text": "Maple Classic Discord Center｜羽田製作\n"
                    f"公告 ID：{announcement_id}"
                }
            }
        ],
    }


def test_cleanup_keeps_verified_new_message_and_deletes_only_old_copy():
    session = Session(
        [
            Response({"id": "123", "channel_id": "456"}),
            Response(
                [
                    message("900", "84087"),
                    message("800", "84087"),
                    message("700", "99999"),
                    message("600", "84087", webhook_id="other"),
                ]
            ),
        ]
    )

    deleted = cleanup.cleanup_obsolete_copies(
        config(), "84087", "900", session=session
    )

    assert deleted == ("800",)
    assert len(session.delete_calls) == 1
    assert session.delete_calls[0][0].endswith("/messages/800")


def test_cleanup_stops_if_keep_message_does_not_match_announcement():
    session = Session(
        [
            Response({"id": "123", "channel_id": "456"}),
            Response([message("800", "84087")]),
        ]
    )

    with pytest.raises(ValueError, match="保留的 Discord 訊息不符合"):
        cleanup.cleanup_obsolete_copies(
            config(), "84087", "900", session=session
        )

    assert session.delete_calls == []
