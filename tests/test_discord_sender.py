import pytest
import requests
import re

from announcement_detail import ImageBlock, TextBlock
from discord_sender import (
    DiscordSendError,
    MAX_CONTENT_EMBEDS,
    MAX_DESCRIPTION_LENGTH,
    MAX_EMBED_FIELD_VALUE_LENGTH,
    MAX_MESSAGE_EMBED_TEXT_LENGTH,
    _embed_text_length,
    _format_announcement_content,
    build_announcement_payloads,
    format_content_descriptions,
    get_category_color,
    get_category_display,
    delete_announcement_messages,
    send_announcement,
    validate_announcement_payloads,
)
from maple_parser import Announcement

WEBHOOK = "https://discord.com/api/webhooks/123456/test-token"


def test_announcement_content_presentation_cleanup_preserves_official_body():
    content = (
        "── 活動內容　 \n"
        "------------------------\n"
        "活動時間： 2026/07/23\n"
        "\n\n\n"
        "──── 注意事項\n"
        "──────────────────────\n"
        "活動方式：　完成任務\n"
        "傳送門\n"
        "【預先下載、事前創角】\n"
        "《新楓之谷：經典版》\n"
        "活動獎勵：道具\n"
        "營運團隊 敬上　 "
    )

    assert _format_announcement_content(content) == (
        "📌 活動內容\n"
        "活動時間： 2026/07/23\n"
        "⚠️ 注意事項\n"
        "活動方式： 完成任務\n"
        "🔗 傳送門\n"
        "▶ 預先下載、事前創角\n"
        "《新楓之谷：經典版》\n"
        "活動獎勵：道具\n"
        "營運團隊 敬上"
    )


def test_payload_body_spacing_matches_compact_82178_layout():
    session = FakeSession([FakeResponse(204)])
    send_announcement(
        WEBHOOK,
        announcement(),
        user_agent="test",
        blocks=(
            TextBlock("親愛的冒險者們：\n\n第一段\n\n第二段\n\n注意事項"),
        ),
        session=session,
    )

    description = session.calls[0][1]["json"]["embeds"][0]["description"]
    assert "\n\n📄 **公告內容**\n" in description
    body = description.split("📄 **公告內容**\n", 1)[1]
    assert body == "親愛的冒險者們：\n第一段\n第二段\n⚠️ 注意事項"
    assert "\n\n" not in body


def test_82273_reward_table_is_readable_in_final_discord_payload():
    item = Announcement(
        "82273",
        "活動",
        "新楓之谷：經典版《0731(五)開服三日感恩回饋公告》",
        "2026/07/31",
        "https://maplestoryclassic.beanfun.com/bulletin?Bid=82273",
    )
    content = (
        "親愛的冒險者們：\n\n"
        "感謝各位冒險者的支持與包容。\n\n"
        "🎁 道具獎勵\n\n"
        "• 經驗值1.5倍券（30分鐘） ×2｜14天\n"
        "• 選擇欄位4格擴充券 ×1｜14天\n"
        "• 蛋糕 ×100｜永久\n"
        "• 回家卷軸 ×10｜永久\n\n"
        "※獎勵領取時間：2026-07-31 ～ 2026-08-09 23:59\n\n"
        "敬祝各位冒險者們遊戲愉快～\n\n"
        "《新楓之谷：經典版》營運團隊 敬上"
    )
    session = FakeSession([FakeResponse(204)])

    send_announcement(
        WEBHOOK,
        item,
        user_agent="test",
        blocks=(TextBlock(content),),
        session=session,
    )

    embeds = session.calls[0][1]["json"]["embeds"]
    description = embeds[0]["description"]
    assert len(embeds) == 1
    assert "🎁 道具獎勵\n\n• 經驗值1.5倍券（30分鐘） ×2｜14天" in description
    assert "• 回家卷軸 ×10｜永久\n\n※獎勵領取時間" in description
    assert "23:59\n\n敬祝各位冒險者們遊戲愉快～" in description
    assert "遊戲愉快～\n\n《新楓之谷：經典版》營運團隊 敬上" in description
    assert "道具名稱\n數量\n期限" not in description
    assert embeds[0]["footer"]["text"] == (
        "Maple Classic Discord Center｜羽田製作\n公告 ID：82273"
    )


def test_link_icons_are_outside_markdown_and_portal_has_one_blank_line():
    support_url = "https://support.example.com/faq"
    result = _format_announcement_content(
        f"整合FAQ : [傳送門]({support_url})"
    )

    assert result == f"整合FAQ：\n\n🔗 [傳送門]({support_url})"
    assert "[🔗 傳送門]" not in result
    assert result.count(support_url) == 1


def test_reliable_social_and_official_hosts_get_external_icons():
    facebook_url = "https://www.facebook.com/maple"
    instagram_url = "https://www.instagram.com/maple/"
    official_url = "https://maplestoryclassic.beanfun.com/bulletin"
    result = _format_announcement_content(
        f"[官方粉絲團]({facebook_url})\n"
        f"[Instagram 官方帳號]({instagram_url})\n"
        f"[官方網站]({official_url})"
    )

    assert f"🍁 [官方粉絲團]({facebook_url})" in result
    assert f"🍁 [Instagram 官方帳號]({instagram_url})" in result
    assert f"🌐 [官方網站]({official_url})" in result
    assert result.count(facebook_url) == 1
    assert result.count(instagram_url) == 1
    assert result.count(official_url) == 1


def test_official_portal_link_uses_portal_icon_instead_of_official_site_icon():
    portal_url = "https://maplestoryclassic-event.beanfun.com/Event/E20260701/Index"
    result = _format_announcement_content(
        f"[事前預約傳送門]({portal_url})"
    )

    assert result == f"🔗 [事前預約傳送門]({portal_url})"


def test_unknown_host_link_does_not_get_a_guessed_icon():
    result = _format_announcement_content(
        "[一般連結](https://example.com/path)"
    )

    assert result == "[一般連結](https://example.com/path)"


def test_bare_support_url_stays_clickable_once_in_payload():
    support_url = "https://games.crm.gamania.com/hc/zh-tw/requests/new"
    session = FakeSession([FakeResponse(204)])

    send_announcement(
        WEBHOOK,
        announcement(),
        user_agent="test",
        content=f"遊戲橘子問題回報中心：\n{support_url}",
        session=session,
    )

    description = session.calls[0][1]["json"]["embeds"][0]["description"]
    assert description.count(support_url) == 1
    assert f"[{support_url}]" not in description
    assert f"({support_url})" not in description


class FakeResponse:
    def __init__(self, status_code=204, text="", json_body=None):
        self.status_code = status_code
        self.text = text
        self.json_body = json_body if json_body is not None else {}

    def json(self):
        return self.json_body


class FakeSession:
    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.calls = []
        self.closed = False

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def close(self):
        self.closed = True


class DeleteSession:
    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.calls = []
        self.closed = False

    def delete(self, url, **kwargs):
        self.calls.append((url, kwargs))
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def close(self):
        self.closed = True


def test_bot_token_publishes_created_webhook_message():
    bot_token = "test-bot-token"
    session = FakeSession(
        [
            FakeResponse(
                200,
                json_body={"id": "456789", "channel_id": "123456"},
            ),
            FakeResponse(200),
        ]
    )
    message_ids = send_announcement(
        WEBHOOK,
        announcement(),
        user_agent="test-agent",
        bot_token=bot_token,
        session=session,
    )

    assert len(session.calls) == 2
    assert message_ids == ("456789",)
    webhook_url, webhook_kwargs = session.calls[0]
    publish_url, publish_kwargs = session.calls[1]
    assert webhook_url == WEBHOOK
    assert webhook_kwargs["params"] == {"wait": "true"}
    assert publish_url == (
        "https://discord.com/api/v10/channels/123456/messages/456789/crosspost"
    )
    assert publish_kwargs == {
        "headers": {
            "Authorization": f"Bot {bot_token}",
            "User-Agent": "test-agent",
        },
        "timeout": 15,
    }


def test_delete_recorded_messages_accepts_deleted_and_already_missing():
    session = DeleteSession([FakeResponse(204), FakeResponse(404)])

    delete_announcement_messages(
        WEBHOOK,
        ("1540344741508685845", "1540344792590843966"),
        user_agent="test",
        session=session,
    )

    assert [call[0] for call in session.calls] == [
        f"{WEBHOOK}/messages/1540344741508685845",
        f"{WEBHOOK}/messages/1540344792590843966",
    ]
    assert not session.closed


def test_delete_recorded_message_retries_429():
    session = DeleteSession(
        [FakeResponse(429, json_body={"retry_after": 0.25}), FakeResponse(204)]
    )
    sleeps = []

    delete_announcement_messages(
        WEBHOOK,
        ("1540344741508685845",),
        user_agent="test",
        session=session,
        sleep=sleeps.append,
    )

    assert len(session.calls) == 2
    assert sleeps == [0.25]


def test_delete_recorded_message_failure_does_not_expose_webhook():
    session = DeleteSession([FakeResponse(403, text=WEBHOOK)])

    with pytest.raises(DiscordSendError) as exc_info:
        delete_announcement_messages(
            WEBHOOK,
            ("1540344741508685845",),
            user_agent="test",
            session=session,
        )

    assert WEBHOOK not in str(exc_info.value)


def test_bot_token_requires_message_and_channel_ids_from_webhook_response():
    session = FakeSession([FakeResponse(200, json_body={"id": "456789"})])
    with pytest.raises(
        DiscordSendError, match="缺少 message_id 或 channel_id"
    ):
        send_announcement(
            WEBHOOK,
            announcement(),
            user_agent="test",
            bot_token="test-bot-token",
            session=session,
        )
    assert len(session.calls) == 1


def test_publish_429_retries_using_retry_after():
    session = FakeSession(
        [
            FakeResponse(
                200,
                json_body={"id": "456789", "channel_id": "123456"},
            ),
            FakeResponse(429, json_body={"retry_after": 0.25}),
            FakeResponse(200),
        ]
    )
    sleeps = []
    send_announcement(
        WEBHOOK,
        announcement(),
        user_agent="test",
        bot_token="test-bot-token",
        session=session,
        sleep=sleeps.append,
    )
    assert len(session.calls) == 3
    assert sleeps == [0.25]


def test_publish_failure_is_safe_and_identifies_chunk():
    bot_token = "secret-bot-token"
    response_text = f"failed {WEBHOOK} {bot_token}"
    session = FakeSession(
        [
            FakeResponse(
                200,
                json_body={"id": "456789", "channel_id": "123456"},
            ),
            FakeResponse(403, text=response_text),
        ]
    )
    with pytest.raises(DiscordSendError) as error:
        send_announcement(
            WEBHOOK,
            announcement(),
            user_agent="test",
            bot_token=bot_token,
            session=session,
        )
    assert "chunk=1/1" in str(error.value)
    assert "HTTP 403" in str(error.value)
    assert WEBHOOK not in str(error.value)
    assert bot_token not in str(error.value)


def test_every_webhook_chunk_is_published_before_sending_the_next_chunk():
    session = FakeSession(
        [
            FakeResponse(200, json_body={"id": "101", "channel_id": "999"}),
            FakeResponse(200),
            FakeResponse(200, json_body={"id": "102", "channel_id": "999"}),
            FakeResponse(200),
        ]
    )
    blocks = (TextBlock("body"),) + tuple(
        ImageBlock(f"https://cdn.example.com/{index}.jpg") for index in range(12)
    )
    send_announcement(
        WEBHOOK,
        announcement(),
        user_agent="test",
        blocks=blocks,
        bot_token="test-bot-token",
        session=session,
    )
    assert [call[0] for call in session.calls] == [
        WEBHOOK,
        "https://discord.com/api/v10/channels/999/messages/101/crosspost",
        WEBHOOK,
        "https://discord.com/api/v10/channels/999/messages/102/crosspost",
    ]


def announcement(title="標題", category="重要", date="2026/07/23", url="https://example.com/1"):
    return Announcement("1", category, title, date, url)


def reconstructed_payload_body(payloads):
    pieces = []
    heading_pattern = re.compile(
        r"📄 \*\*公告內容(?:（\d+/\d+）)?\*\*\n"
    )
    for payload in payloads:
        for embed in payload["embeds"]:
            description = embed.get("description")
            if not description or description == "\u200b":
                continue
            if match := heading_pattern.search(description):
                description = description[match.end() :]
            pieces.append(description)
    return "".join(pieces)


def long_sanction_content(row_count=500):
    rows = "\n".join(
        f"• 角色名稱：測試角色{index:04d}\n  制裁結果：永久鎖定"
        for index in range(row_count)
    )
    return (
        "親愛的冒險者們：\n"
        "以下為遊戲異常行為制裁名單。\n"
        f"{rows}\n"
        "營運團隊重申與叮嚀：\n"
        "請冒險者切勿使用任何非官方授權之輔助程式。\n"
        "《新楓之谷：經典版》營運團隊 敬上"
    )


def test_82279_long_body_builds_multiple_legal_lossless_payloads():
    item = Announcement(
        "82279",
        "重要",
        "新楓之谷：經典版《0802(日)遊戲異常行為制裁公告》",
        "2026/08/02",
        "https://maplestoryclassic.beanfun.com/bulletin?Bid=82279",
    )
    content = long_sanction_content()
    payloads = build_announcement_payloads(
        item,
        blocks=(TextBlock(content),),
    )

    assert len(content) > 6000
    assert len(payloads) > 1
    validate_announcement_payloads(item, payloads)
    assert reconstructed_payload_body(payloads) == _format_announcement_content(content)

    all_embeds = [embed for payload in payloads for embed in payload["embeds"]]
    descriptions = [
        embed.get("description", "")
        for embed in all_embeds
        if embed.get("description")
    ]
    for chunk_index, payload in enumerate(payloads, start=1):
        embeds = payload["embeds"]
        assert len(embeds) <= MAX_CONTENT_EMBEDS
        assert sum(_embed_text_length(embed) for embed in embeds) <= MAX_MESSAGE_EMBED_TEXT_LENGTH
        assert all(
            len(embed.get("description", "")) <= MAX_DESCRIPTION_LENGTH
            for embed in embeds
        )
        combined = "\n".join(embed.get("description", "") for embed in embeds)
        assert f"公告內容（{chunk_index}/{len(payloads)}）" in combined

    assert "title" in payloads[0]["embeds"][0]
    assert "author" in payloads[0]["embeds"][0]
    assert "公告分類：重要" in payloads[0]["embeds"][0]["description"]
    assert all(
        "title" not in embed and "author" not in embed
        for payload in payloads[1:]
        for embed in payload["embeds"]
    )
    assert all(
        "公告分類：" not in embed.get("description", "")
        for payload in payloads[1:]
        for embed in payload["embeds"]
    )
    assert all(
        "footer" not in embed
        for payload in payloads[:-1]
        for embed in payload["embeds"]
    )
    assert payloads[-1]["embeds"][-1]["footer"]["text"].endswith(
        "公告 ID：82279"
    )

    for index in (0, 1, 249, 499):
        row = f"• 角色名稱：測試角色{index:04d}\n  制裁結果：永久鎖定"
        assert sum(row in description for description in descriptions) == 1


def test_82289_scale_body_builds_legal_lossless_payloads():
    item = Announcement(
        "82289",
        "重要",
        "新楓之谷：經典版《0803(一)遊戲異常行為制裁公告》",
        "2026/08/03",
        "https://maplestoryclassic.beanfun.com/bulletin?Bid=82289",
    )
    content = long_sanction_content(15_805)

    payloads = build_announcement_payloads(item, blocks=(TextBlock(content),))

    assert len(content) > 100_000
    assert len(payloads) > 100
    validate_announcement_payloads(item, payloads)
    assert reconstructed_payload_body(payloads) == _format_announcement_content(content)
    assert all(
        len(payload["embeds"]) <= MAX_CONTENT_EMBEDS
        and sum(_embed_text_length(embed) for embed in payload["embeds"])
        <= MAX_MESSAGE_EMBED_TEXT_LENGTH
        and all(
            len(embed.get("description", "")) <= MAX_DESCRIPTION_LENGTH
            for embed in payload["embeds"]
        )
        for payload in payloads
    )
    assert all(
        "footer" not in embed
        for payload in payloads[:-1]
        for embed in payload["embeds"]
    )
    assert payloads[-1]["embeds"][-1]["footer"]["text"].endswith(
        "公告 ID：82289"
    )

    descriptions = [
        embed.get("description", "")
        for payload in payloads
        for embed in payload["embeds"]
    ]
    for index in (0, 1, 7_902, 15_804):
        row = f"• 角色名稱：測試角色{index:04d}\n  制裁結果：永久鎖定"
        assert sum(row in description for description in descriptions) == 1


def test_compact_sanction_announcement_builds_one_payload_without_chunk_number():
    item = Announcement(
        "82309",
        "重要",
        "新楓之谷：經典版《0804(二)遊戲異常行為制裁公告》",
        "2026/08/04",
        "https://maplestoryclassic.beanfun.com/bulletin?Bid=82309",
    )
    content = (
        "親愛的冒險者們：\n"
        "為了維護優良的遊戲環境與確保全體玩家的公平性，營運團隊持續進行查緝。\n"
        "以下帳號因嚴重違反遊戲規章，已執行「永久鎖定」處分。\n"
        "處置對象角色數量：共18,924名\n"
        "營運團隊重申與叮嚀：\n"
        "請冒險者切勿抱持僥倖心態，安裝或使用任何非官方授權之輔助程式。\n"
        "感謝大家的配合與支持！\n"
        "《新楓之谷：經典版》營運團隊 敬上"
    )

    payloads = build_announcement_payloads(item, blocks=(TextBlock(content),))

    assert len(payloads) == 1
    validate_announcement_payloads(item, payloads)
    description = payloads[0]["embeds"][0]["description"]
    assert "📄 **公告內容**" in description
    assert "公告內容（1/" not in description
    assert "處置對象角色數量：共18,924名" in description
    assert "角色名稱：" not in description
    assert payloads[0]["embeds"][-1]["footer"]["text"].endswith(
        "公告 ID：82309"
    )


def test_long_payloads_preserve_text_and_image_block_order():
    first_image = "https://cdn.example.com/first.jpg"
    second_image = "https://cdn.example.com/second.jpg"
    blocks = (
        TextBlock("圖片前\n" + "長段落內容。" * 800),
        ImageBlock(first_image),
        TextBlock("圖片間文字"),
        ImageBlock(second_image),
        TextBlock("最後文字"),
    )
    payloads = build_announcement_payloads(announcement(), blocks=blocks)
    flattened = [embed for payload in payloads for embed in payload["embeds"]]
    first_index = next(
        index
        for index, embed in enumerate(flattened)
        if embed.get("image", {}).get("url") == first_image
    )
    second_index = next(
        index
        for index, embed in enumerate(flattened)
        if embed.get("image", {}).get("url") == second_image
    )

    assert first_index < second_index
    assert "圖片前" in "".join(
        embed.get("description", "") for embed in flattened[:first_index]
    )
    assert "圖片間文字" in "".join(
        embed.get("description", "")
        for embed in flattened[first_index + 1 : second_index]
    )
    assert "最後文字" in "".join(
        embed.get("description", "") for embed in flattened[second_index + 1 :]
    )
    assert all("footer" not in embed for embed in flattened[:-1])
    assert flattened[-1]["footer"]["text"].endswith("公告 ID：1")


def test_long_payload_split_keeps_markdown_link_and_chinese_text_intact():
    link = "[客服中心](https://support.example.com/very/long/path)"
    content = "這是長篇公告內容。" * 450 + f"\n{link}\n" + "後續中文內容。" * 450
    payloads = build_announcement_payloads(
        announcement(), blocks=(TextBlock(content),)
    )
    descriptions = [
        embed.get("description", "")
        for payload in payloads
        for embed in payload["embeds"]
    ]

    assert sum(link in description for description in descriptions) == 1
    assert reconstructed_payload_body(payloads) == _format_announcement_content(content)


def test_preflight_rejects_message_total_over_6000():
    payload = {
        "username": "Maple Classic Bot",
        "embeds": [
            {"description": "a" * 3000},
            {"description": "b" * 3000, "title": "超限"},
        ],
        "allowed_mentions": {"parse": []},
    }

    with pytest.raises(DiscordSendError, match="Embed 總文字長度") as error:
        validate_announcement_payloads(announcement(), [payload])

    assert "ID=1" in str(error.value)
    assert "chunk=1/1" in str(error.value)


def test_preflight_rejects_oversized_field_value():
    payload = {
        "username": "Maple Classic Bot",
        "embeds": [
            {
                "description": "正文",
                "fields": [
                    {"name": "欄位", "value": "x" * (MAX_EMBED_FIELD_VALUE_LENGTH + 1)}
                ],
            }
        ],
        "allowed_mentions": {"parse": []},
    }

    with pytest.raises(DiscordSendError, match="field 1 value 超限"):
        validate_announcement_payloads(announcement(), [payload])


def test_send_preflights_all_payloads_before_first_webhook_request(monkeypatch):
    invalid_payload = {
        "username": "Maple Classic Bot",
        "embeds": [{"description": "x" * (MAX_DESCRIPTION_LENGTH + 1)}],
        "allowed_mentions": {"parse": []},
    }
    monkeypatch.setattr(
        "discord_sender.build_announcement_payloads",
        lambda *_args, **_kwargs: [invalid_payload],
    )
    session = FakeSession([])

    with pytest.raises(DiscordSendError, match="發送前驗證失敗"):
        send_announcement(WEBHOOK, announcement(), user_agent="test", session=session)

    assert session.calls == []


def test_multi_payload_send_logs_message_ids_and_stops_at_failed_chunk(caplog):
    content = long_sanction_content(260)
    payloads = build_announcement_payloads(
        announcement(), blocks=(TextBlock(content),)
    )
    assert len(payloads) > 1
    session = FakeSession(
        [FakeResponse(200, json_body={"id": "first-message"}), FakeResponse(400)]
    )

    with caplog.at_level("INFO"), pytest.raises(DiscordSendError, match="chunk=2/"):
        send_announcement(
            WEBHOOK,
            announcement(),
            user_agent="test",
            blocks=(TextBlock(content),),
            session=session,
        )

    assert len(session.calls) == 2
    assert all(call[1]["params"] == {"wait": "true"} for call in session.calls)
    assert "message_id=first-message" in caplog.text
    assert "Discord chunk failed" in caplog.text


def test_all_chunks_are_sent_once_and_each_success_message_id_is_logged(caplog):
    content = long_sanction_content(260)
    payloads = build_announcement_payloads(
        announcement(), blocks=(TextBlock(content),)
    )
    expected_message_ids = tuple(
        str(1540345000000000000 + index)
        for index in range(1, len(payloads) + 1)
    )
    session = FakeSession(
        [
            FakeResponse(200, json_body={"id": message_id})
            for message_id in expected_message_ids
        ]
    )

    with caplog.at_level("INFO"):
        message_ids = send_announcement(
            WEBHOOK,
            announcement(),
            user_agent="test",
            blocks=(TextBlock(content),),
            session=session,
        )

    assert len(session.calls) == len(payloads)
    assert message_ids == expected_message_ids
    for message_id in expected_message_ids:
        assert f"message_id={message_id}" in caplog.text


@pytest.mark.parametrize(
    ("category", "expected_color"),
    [
        ("活動", 0xB53A2D),
        ("更新", 0xD8B400),
        ("重要", 0x2D63A8),
        ("綜合", 0x6F6F6F),
        ("未知分類", 0x95A5A6),
        ("", 0x95A5A6),
        (None, 0x95A5A6),
        (" 活動 ", 0xB53A2D),
    ],
)
def test_get_category_color(category, expected_color):
    assert get_category_color(category) == expected_color


@pytest.mark.parametrize(
    ("category", "expected_color"),
    [
        ("活動", 0xB53A2D),
        ("更新", 0xD8B400),
        ("重要", 0x2D63A8),
        ("綜合", 0x6F6F6F),
        ("未知分類", 0x95A5A6),
    ],
)
def test_payload_embed_color_matches_category(category, expected_color):
    session = FakeSession([FakeResponse(204)])
    send_announcement(
        WEBHOOK,
        announcement(category=category),
        user_agent="test",
        session=session,
    )
    assert session.calls[0][1]["json"]["embeds"][0]["color"] == expected_color


@pytest.mark.parametrize(
    ("category", "expected_display"),
    [
        ("活動", "📅 活動"),
        ("更新", "🔧 更新"),
        ("重要", "🚨 重要"),
        ("綜合", "📢 綜合"),
        ("Unknown", "📢 Unknown"),
        (" 活動 ", "📅 活動"),
    ],
)
def test_get_category_display(category, expected_display):
    assert get_category_display(category) == expected_display


@pytest.mark.parametrize(
    ("category", "expected_color", "expected_display"),
    [
        ("活動", 0xB53A2D, "活動"),
        ("更新", 0xD8B400, "更新"),
        ("重要", 0x2D63A8, "重要"),
        ("綜合", 0x6F6F6F, "綜合"),
        ("未知分類", 0x95A5A6, "未知分類"),
    ],
)
def test_payload_embed_category_matches_category(category, expected_color, expected_display):
    session = FakeSession([FakeResponse(204)])
    send_announcement(
        WEBHOOK,
        announcement(category=category),
        user_agent="test",
        session=session,
    )
    embed = session.calls[0][1]["json"]["embeds"][0]
    assert embed["color"] == expected_color
    assert f"公告分類：{expected_display}" in embed["description"]


@pytest.mark.parametrize(
    ("category", "expected_prefix"),
    [
        ("活動", "📅 "),
        ("更新", "🔧 "),
        ("重要", "🚨 "),
        ("綜合", "📢 "),
        ("Unknown", "📢 "),
        ("", "📢 "),
        (" 活動 ", "📅 "),
    ],
)
def test_payload_embed_title_starts_with_category_icon(category, expected_prefix):
    session = FakeSession([FakeResponse(204)])
    send_announcement(
        WEBHOOK,
        announcement(title="公告標題", category=category),
        user_agent="test",
        session=session,
    )
    assert session.calls[0][1]["json"]["embeds"][0]["title"] == f"{expected_prefix}公告標題"


def test_normal_204_success_payload_and_mentions():
    session = FakeSession([FakeResponse(204)])
    send_announcement(WEBHOOK, announcement(), user_agent="test", session=session)
    payload = session.calls[0][1]["json"]
    assert "discord" not in payload["username"].lower()
    assert payload["allowed_mentions"] == {"parse": []}
    assert payload["embeds"][0]["title"] == "🚨 標題"
    embed = payload["embeds"][0]
    assert "fields" not in embed
    assert embed["description"] == "🏷️ 公告分類：重要　　📅 公告日期：2026/07/23\n\n"
    assert "公告分類：" in embed["description"]
    assert "公告日期：" in embed["description"]
    assert "官方公告" not in embed["description"]
    assert "公告編號" not in embed["description"]
    assert embed["author"] == {"name": "新楓之谷：經典版官方消息"}
    assert embed["footer"] == {
        "text": "Maple Classic Discord Center｜羽田製作\n公告 ID：1"
    }
    assert "thumbnail" not in embed
    assert session.closed is False


def test_history_mode_adds_history_footer_line_only():
    session = FakeSession([FakeResponse(204)])
    send_announcement(WEBHOOK, announcement(), user_agent="test", history_mode=True, session=session)

    assert session.calls[0][1]["json"]["embeds"][0]["footer"]["text"] == (
        "Maple Classic Discord Center｜羽田製作\n🏞️ 歷史公告\n公告 ID：1"
    )


def test_thumbnail_url_adds_embed_author_and_footer_icons():
    session = FakeSession([FakeResponse(204)])
    thumbnail_url = "https://cdn.example.com/maple-logo"

    send_announcement(
        WEBHOOK,
        announcement(),
        user_agent="test",
        thumbnail_url=thumbnail_url,
        session=session,
    )

    embed = session.calls[0][1]["json"]["embeds"][0]
    assert embed["thumbnail"] == {"url": thumbnail_url}
    assert embed["author"]["icon_url"] == thumbnail_url
    assert embed["footer"]["icon_url"] == thumbnail_url


@pytest.mark.parametrize(
    "thumbnail_url",
    ["http://cdn.example.com/logo", "https:///logo", "javascript:alert(1)"],
)
def test_invalid_thumbnail_url_is_rejected_without_leaking_url(thumbnail_url):
    with pytest.raises(DiscordSendError) as error:
        send_announcement(
            WEBHOOK,
            announcement(),
            user_agent="test",
            thumbnail_url=thumbnail_url,
            session=FakeSession([]),
        )

    assert thumbnail_url not in str(error.value)


@pytest.mark.parametrize(
    "url",
    ["", "http://discord.com/api/webhooks/1/x", "https://example.com/api/webhooks/1/x", "https://discord.com/not-webhook"],
)
def test_invalid_webhook_urls_are_rejected(url):
    with pytest.raises(DiscordSendError):
        send_announcement(url, announcement(), user_agent="test", session=FakeSession([]))


def test_http_400_does_not_retry_or_leak_url():
    session = FakeSession([FakeResponse(400, f"bad request {WEBHOOK}")])
    with pytest.raises(DiscordSendError) as error:
        send_announcement(WEBHOOK, announcement(), user_agent="test", session=session)
    assert len(session.calls) == 1
    assert WEBHOOK not in str(error.value)
    assert "HTTP 400" in str(error.value)


def test_429_retries_using_retry_after():
    session = FakeSession([FakeResponse(429, json_body={"retry_after": 0.25}), FakeResponse(204)])
    sleeps = []
    send_announcement(WEBHOOK, announcement(), user_agent="test", session=session, sleep=sleeps.append)
    assert len(session.calls) == 2
    assert sleeps == [0.25]


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_retryable_server_errors_retry(status):
    session = FakeSession([FakeResponse(status), FakeResponse(204)])
    sleeps = []
    send_announcement(WEBHOOK, announcement(), user_agent="test", session=session, sleep=sleeps.append)
    assert len(session.calls) == 2
    assert sleeps == [1.0]


@pytest.mark.parametrize("error", [requests.Timeout("timeout"), requests.ConnectionError("connection")])
def test_timeout_and_connection_error_retry(error):
    session = FakeSession([error, FakeResponse(204)])
    sleeps = []
    send_announcement(WEBHOOK, announcement(), user_agent="test", session=session, sleep=sleeps.append)
    assert len(session.calls) == 2
    assert sleeps == [1.0]


def test_retries_are_limited_and_final_error_is_safe():
    session = FakeSession([FakeResponse(500, "failed")] * 3)
    sleeps = []
    with pytest.raises(DiscordSendError) as error:
        send_announcement(WEBHOOK, announcement(), user_agent="test", session=session, sleep=sleeps.append)
    assert len(session.calls) == 3
    assert sleeps == [1.0, 2.0]
    assert WEBHOOK not in str(error.value)


def test_embed_values_are_safely_truncated():
    session = FakeSession([FakeResponse(204)])
    send_announcement(
        WEBHOOK,
        announcement(title="x" * 300, category="y" * 1100, date="z" * 1100, url="https://example.com/" + "u" * 1100),
        user_agent="test",
        session=session,
    )
    embed = session.calls[0][1]["json"]["embeds"][0]
    assert len(embed["title"]) == 256
    assert len(embed["author"]["name"]) <= 256
    assert "fields" not in embed
    assert embed["description"] and len(embed["description"]) <= 4096
    assert embed["footer"]["text"] and len(embed["footer"]["text"]) <= 2048


def test_embed_title_truncation_preserves_category_icon():
    session = FakeSession([FakeResponse(204)])
    send_announcement(
        WEBHOOK,
        announcement(title="x" * 300, category="活動"),
        user_agent="test",
        session=session,
    )
    title = session.calls[0][1]["json"]["embeds"][0]["title"]
    assert title == f"📅 {'x' * 253}…"
    assert len(title) == 256
    assert title.startswith("📅 ")


def test_owned_session_is_closed(monkeypatch):
    session = FakeSession([FakeResponse(204)])
    monkeypatch.setattr("discord_sender.requests.Session", lambda: session)
    send_announcement(WEBHOOK, announcement(), user_agent="test")
    assert session.closed is True


def portal_content() -> str:
    return (
        "傳送門\n"
        "【\n"
        "[新楓之谷：經典版 官方粉絲團](https://facebook.example.com/maple)\n"
        "】\n"
        "【\n"
        "[新楓之谷：經典版 Instagram 官方帳號](https://instagram.example.com/maple)\n"
        "】"
    )


def test_portal_links_are_markdown_without_wrapper_brackets():
    result = _format_announcement_content(portal_content())

    assert result == (
        "🔗 傳送門\n\n"
        "[新楓之谷：經典版 官方粉絲團](https://facebook.example.com/maple)\n"
        "[新楓之谷：經典版 Instagram 官方帳號](https://instagram.example.com/maple)"
    )
    assert "【\n" not in result and "\n】" not in result
    assert "🍁" not in result
    assert result.count("https://facebook.example.com/maple") == 1
    assert result.count("https://instagram.example.com/maple") == 1


def test_portal_link_blocks_are_unwrapped_with_one_blank_line_before_links():
    content = (
        "傳送門\n\n"
        "【\n"
        "[新楓之谷：經典版 官方粉絲團](https://facebook.example.com/maple)\n"
        "】\n\n"
        "【\n"
        "[新楓之谷：經典版 Instagram 官方帳號](https://instagram.example.com/maple)\n"
        "】"
    )

    assert _format_announcement_content(content) == (
        "🔗 傳送門\n\n"
        "[新楓之谷：經典版 官方粉絲團](https://facebook.example.com/maple)\n"
        "[新楓之谷：經典版 Instagram 官方帳號](https://instagram.example.com/maple)"
    )


def test_portal_markdown_links_are_in_discord_payload_without_duplicate_urls():
    session = FakeSession([FakeResponse(204)])
    send_announcement(WEBHOOK, announcement(), user_agent="test", content=portal_content(), session=session)

    description = session.calls[0][1]["json"]["embeds"][0]["description"]
    assert "[新楓之谷：經典版 官方粉絲團](https://facebook.example.com/maple)" in description
    assert "[新楓之谷：經典版 Instagram 官方帳號](https://instagram.example.com/maple)" in description
    assert description.count("https://facebook.example.com/maple") == 1
    assert description.count("https://instagram.example.com/maple") == 1


def test_non_portal_markdown_link_does_not_gain_maple_emoji():
    result = _format_announcement_content("一般說明\n[官方網站](https://example.com)")

    assert "[官方網站](https://example.com)" in result
    assert "🍁 [官方網站]" not in result


def test_long_content_descriptions_do_not_split_markdown_links():
    link = "[新楓之谷：經典版 官方粉絲團](https://facebook.example.com/maple)"
    descriptions = format_content_descriptions(
        announcement(), "x" * 4050 + "\n" + portal_content()
    )

    assert 1 < len(descriptions) <= 10
    assert all(len(description) <= 4096 for description in descriptions)
    assert sum(link in description for description in descriptions) == 1
    assert sum(description.count("[") for description in descriptions) == 2

def test_ordered_blocks_send_text_then_images_then_trailing_text():
    session = FakeSession([FakeResponse(204)])
    send_announcement(
        WEBHOOK,
        announcement(),
        user_agent="test",
        blocks=(
            TextBlock("intro text\nsetup guide:"),
            ImageBlock("https://cdn.example.com/guide-1.jpg"),
            ImageBlock("https://cdn.example.com/guide-2.jpg"),
            TextBlock("closing text"),
        ),
        session=session,
    )
    embeds = session.calls[0][1]["json"]["embeds"]
    assert "intro text" in embeds[0]["description"]
    assert "setup guide:" in embeds[0]["description"]
    assert embeds[1] == {"image": {"url": "https://cdn.example.com/guide-1.jpg"}}
    assert embeds[2] == {"image": {"url": "https://cdn.example.com/guide-2.jpg"}}
    assert "closing text" in embeds[3]["description"]
    assert all("footer" not in embed for embed in embeds[:3])
    assert embeds[3]["footer"]["text"].endswith("公告 ID：1")


def test_82176_golden_payload_keeps_footer_after_images_and_short_closing_text():
    item = Announcement(
        "82176",
        "活動",
        "事前創角活動提醒公告",
        "2026/07/22",
        "https://maplestoryclassic.beanfun.com/bulletin?Bid=82176",
    )
    image_one = "https://tw.hicdn.beanfun.com/beanfun/WebImage/1784825248264.jpg"
    image_two = "https://tw.hicdn.beanfun.com/beanfun/WebImage/1784754689387.jpg"
    session = FakeSession([FakeResponse(204)])

    send_announcement(
        WEBHOOK,
        item,
        user_agent="test",
        history_mode=True,
        blocks=(
            TextBlock("親愛的冒險者們：\n\n公告正文\n\n事前創角教學 :"),
            ImageBlock(image_one),
            ImageBlock(image_two),
            TextBlock("《新楓之谷：經典版》營運團隊 敬上"),
        ),
        session=session,
    )

    embeds = session.calls[0][1]["json"]["embeds"]
    assert len(embeds) == 4
    assert embeds[0]["description"].endswith("事前創角教學：")
    assert embeds[1] == {"image": {"url": image_one}}
    assert embeds[2] == {"image": {"url": image_two}}
    assert embeds[3]["description"] == "《新楓之谷：經典版》營運團隊 敬上"
    assert all("footer" not in embed for embed in embeds[:3])
    assert embeds[3]["footer"]["text"] == (
        "Maple Classic Discord Center｜羽田製作\n"
        "🏞️ 歷史公告\n"
        "公告 ID：82176"
    )


def test_82221_golden_payload_has_clean_unique_markdown_links_and_final_footer():
    facebook_url = "https://www.facebook.com/profile.php?id=61590171137957"
    instagram_url = "https://www.instagram.com/maplestory_classic_tw/"
    item = Announcement(
        "82221",
        "活動",
        "事前創角活動截止公告",
        "2026/07/28",
        "https://maplestoryclassic.beanfun.com/bulletin?Bid=82221",
    )
    session = FakeSession([FakeResponse(204)])

    send_announcement(
        WEBHOOK,
        item,
        user_agent="test",
        history_mode=True,
        blocks=(
            TextBlock(
                "公告正文\n\n"
                f"【[新楓之谷：經典版 官方粉絲團]({facebook_url})\n】\n\n"
                f"【[新楓之谷：經典版 Instagram 官方帳號]({instagram_url})\n】\n\n"
                "《新楓之谷：經典版》營運團隊 敬上"
            ),
        ),
        session=session,
    )

    embeds = session.calls[0][1]["json"]["embeds"]
    combined = "\n".join(embed.get("description", "") for embed in embeds)
    assert f"[新楓之谷：經典版 官方粉絲團]({facebook_url})" in combined
    assert f"[新楓之谷：經典版 Instagram 官方帳號]({instagram_url})" in combined
    assert combined.count(facebook_url) == 1
    assert combined.count(instagram_url) == 1
    assert not any(line.strip() in {"【", "】", "[", "]"} for line in combined.splitlines())
    assert "\n\n\n" not in combined
    assert all("footer" not in embed for embed in embeds[:-1])
    assert embeds[-1]["footer"]["text"].endswith("公告 ID：82221")


def test_last_image_block_adds_a_footer_only_closing_embed():
    session = FakeSession([FakeResponse(204)])
    send_announcement(
        WEBHOOK,
        announcement(),
        user_agent="test",
        blocks=(
            TextBlock("body"),
            ImageBlock("https://cdn.example.com/final.jpg"),
        ),
        session=session,
    )

    embeds = session.calls[0][1]["json"]["embeds"]
    assert len(embeds) == 3
    assert embeds[1] == {"image": {"url": "https://cdn.example.com/final.jpg"}}
    assert embeds[2]["description"] == "\u200b"
    assert "footer" not in embeds[0]
    assert embeds[2]["footer"]["text"].endswith("公告 ID：1")


def test_more_than_ten_ordered_embeds_continue_in_a_second_payload_without_loss():
    session = FakeSession([FakeResponse(204), FakeResponse(204)])
    blocks = (TextBlock("body"),) + tuple(ImageBlock(f"https://cdn.example.com/{index}.jpg") for index in range(12))
    send_announcement(WEBHOOK, announcement(), user_agent="test", blocks=blocks, session=session)

    payloads = [call[1]["json"] for call in session.calls]
    assert len(payloads) == 2
    assert all(len(payload["embeds"]) <= MAX_CONTENT_EMBEDS for payload in payloads)
    assert "body" in payloads[0]["embeds"][0]["description"]
    image_urls = [
        embed["image"]["url"]
        for payload in payloads
        for embed in payload["embeds"]
        if "image" in embed
    ]
    assert image_urls == [f"https://cdn.example.com/{index}.jpg" for index in range(12)]
    assert all(
        "footer" not in embed
        for payload in payloads[:-1]
        for embed in payload["embeds"]
    )
    assert payloads[-1]["embeds"][-1]["footer"]["text"].endswith("公告 ID：1")


def test_long_text_block_splits_before_its_following_image_and_trailing_text():
    session = FakeSession([FakeResponse(204)])
    blocks = (
        TextBlock("a" * 5000),
        ImageBlock("https://cdn.example.com/ordered.jpg"),
        TextBlock("after image"),
    )
    send_announcement(WEBHOOK, announcement(), user_agent="test", blocks=blocks, session=session)

    embeds = session.calls[0][1]["json"]["embeds"]
    image_index = next(index for index, embed in enumerate(embeds) if "image" in embed)
    assert all(len(embed["description"]) <= 4096 for embed in embeds if "description" in embed)
    assert embeds[image_index] == {"image": {"url": "https://cdn.example.com/ordered.jpg"}}
    assert "after image" in embeds[image_index + 1]["description"]


def test_invalid_image_urls_do_not_prevent_text_delivery():
    session = FakeSession([FakeResponse(204)])
    send_announcement(
        WEBHOOK,
        announcement(),
        user_agent="test",
        content="正文",
        images=("data:image/gif;base64,x", "not-a-url"),
        session=session,
    )
    embeds = session.calls[0][1]["json"]["embeds"]
    assert len(embeds) == 1
    assert "image" not in embeds[0]
    assert "正文" in embeds[0]["description"]


def test_full_page_template_is_rejected_before_webhook_payload_is_created():
    session = FakeSession([])
    template = (
        '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN">'
        "<html><body>"
        "Start search bar End search bar "
        "start google ad end google ad "
        "Begin: Pagination End: Pagination comScore"
        "</body></html>"
    )

    with pytest.raises(DiscordSendError) as error:
        send_announcement(
            WEBHOOK,
            announcement(title="不應發送"),
            user_agent="test",
            content=template,
            session=session,
        )

    assert session.calls == []
    assert "ID=1" in str(error.value)
    assert "title=不應發送" in str(error.value)
    assert "疑似完整網站模板" in str(error.value)
