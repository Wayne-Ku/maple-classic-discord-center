"""Send announcement embeds through a Discord webhook."""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Sequence
from urllib.parse import urlsplit

import requests

from config import validate_discord_spacer_emoji, validate_https_image_url
from announcement_detail import (
    AnnouncementContentBlock,
    ImageBlock,
    TextBlock,
    template_garbage_markers,
)
from maple_parser import Announcement


class DiscordSendError(RuntimeError):
    """Raised when Discord does not accept a webhook message."""


MAX_ATTEMPTS = 3
MAX_RETRY_AFTER_SECONDS = 30.0
MAX_DESCRIPTION_LENGTH = 4096
MAX_CONTENT_EMBEDS = 10
MAX_EMBED_TITLE_LENGTH = 256
MAX_EMBED_FIELD_NAME_LENGTH = 256
MAX_EMBED_FIELD_VALUE_LENGTH = 1024
MAX_EMBED_FOOTER_LENGTH = 2048
MAX_EMBED_AUTHOR_LENGTH = 256
MAX_MESSAGE_EMBED_TEXT_LENGTH = 6000
TARGET_DESCRIPTION_BODY_LENGTH = 3500
TARGET_MESSAGE_BODY_TEXT_LENGTH = 5200
MAX_RAW_ENTRIES_PER_MESSAGE = 8
MAX_EMBED_FIELDS = 25
LOGGER = logging.getLogger("maple-classic-discord-center")
CATEGORY_COLORS = {
    "活動": 0xB53A2D,
    "更新": 0xD8B400,
    "重要": 0x2D63A8,
    "綜合": 0x6F6F6F,
}
CATEGORY_ICONS = {
    "活動": "📅",
    "更新": "🔧",
    "重要": "🚨",
    "綜合": "📢",
}
DEFAULT_CATEGORY_COLOR = 0x95A5A6
DEFAULT_CATEGORY_ICON = "📢"
DISCORD_WEBHOOK_HOSTS = {
    "discord.com",
    "discordapp.com",
    "canary.discord.com",
    "ptb.discord.com",
}
DISCORD_API_BASE_URL = "https://discord.com/api/v10"

_SECTION_HEADING_REPLACEMENTS = {
    "活動內容": "📌 活動內容",
    "注意事項": "⚠️ 注意事項",
    "傳送門": "🔗 傳送門",
}
_DASHED_SECTION_HEADING_RE = re.compile(r"^[─-]{2,}\s*(活動內容|注意事項)\s*$")
_BRACKETED_SECTION_HEADING_RE = re.compile(r"^【\s*(.+?)\s*】$")
_SEPARATOR_LINE_RE = re.compile(r"^\s*[─-][\s─-]*$")
_MARKDOWN_LINK_BLOCK_RE = re.compile(
    r"(?m)^[ \t]*[【\[][ \t]*(?:\n[ \t]*)?"
    r"(?P<link>\[[^\]\n]+\]\(https?://[^\s)]+\))[ \t]*(?:\n[ \t]*)?"
    r"[】\]][ \t]*$"
)
_MARKDOWN_LINK_RE = re.compile(
    r"(?P<link>\[(?P<label>[^\]\n]+)\]\((?P<href>https?://[^\s)]+)\))"
)


def _format_announcement_content(content: str) -> str:
    """Apply presentation-only cleanup to an already retrieved announcement body."""
    lines: list[str] = []
    normalized_content = content.replace("\r\n", "\n").replace("\r", "\n")
    normalized_content = _MARKDOWN_LINK_BLOCK_RE.sub(r"\g<link>", normalized_content)
    normalized_content = re.sub(
        r"\n{2,}(?=\[[^\]\n]+\]\(https?://[^\s)]+\))",
        "\n",
        normalized_content,
    )
    for line in normalized_content.split("\n"):
        line = line.replace("　", " ").rstrip()
        stripped_line = line.strip()

        if _SEPARATOR_LINE_RE.fullmatch(line):
            continue
        if match := _DASHED_SECTION_HEADING_RE.fullmatch(stripped_line):
            lines.append(_SECTION_HEADING_REPLACEMENTS[match.group(1)])
            continue
        if stripped_line in _SECTION_HEADING_REPLACEMENTS:
            lines.append(_SECTION_HEADING_REPLACEMENTS[stripped_line])
            continue
        if re.fullmatch(r"事前創角教學\s*:", stripped_line):
            lines.append("事前創角教學：")
            continue
        if match := _BRACKETED_SECTION_HEADING_RE.fullmatch(stripped_line):
            lines.append(f"▶ {match.group(1)}")
            continue
        lines.append(line)

    formatted_lines: list[str] = []
    previous_was_link = False
    for index, line in enumerate(lines):
        stripped_line = line.strip()
        if not stripped_line:
            previous_line = formatted_lines[-1] if formatted_lines else ""
            next_line = next(
                (candidate.strip() for candidate in lines[index + 1 :] if candidate.strip()),
                "",
            )
            table_boundary = (
                next_line == "🎁 道具獎勵"
                or next_line.startswith("• ")
                or previous_line == "🎁 道具獎勵"
                or previous_line.startswith("• ")
                or previous_line.startswith("  ")
                or "🎁 道具獎勵" in formatted_lines
            )
            if table_boundary and formatted_lines and formatted_lines[-1] != "":
                formatted_lines.append("")
            continue
        matches = list(_MARKDOWN_LINK_RE.finditer(stripped_line))
        if len(matches) == 1:
            match = matches[0]
            prefix = stripped_line[: match.start()].rstrip()
            suffix = stripped_line[match.end() :].strip()
            icon = _link_icon(match.group("label"), match.group("href"))
            link_line = f"{icon} {match.group('link')}" if icon else match.group("link")
            if prefix:
                if re.search(r"FAQ\s*:$", prefix):
                    prefix = re.sub(r"\s*:\s*$", "：", prefix)
                formatted_lines.append(prefix)
                formatted_lines.append("")
            elif formatted_lines and not previous_was_link and formatted_lines[-1] != "":
                formatted_lines.append("")
            formatted_lines.append(link_line)
            if suffix:
                formatted_lines.append(suffix)
            previous_was_link = True
            continue
        formatted_lines.append(
            f"  {stripped_line}" if line.startswith("  ") else stripped_line
        )
        previous_was_link = False

    return "\n".join(formatted_lines).strip()


def _link_icon(label: str, href: str) -> str | None:
    """Return a stable Unicode icon only for reliably identifiable links."""
    hostname = (urlsplit(href).hostname or "").lower()
    if "傳送門" in label:
        return "🔗"
    if hostname == "facebook.com" or hostname.endswith(".facebook.com"):
        return "🍁"
    if hostname == "instagram.com" or hostname.endswith(".instagram.com"):
        return "🍁"
    if hostname in {
        "maplestoryclassic.beanfun.com",
        "maplestoryclassic-event.beanfun.com",
    }:
        return "🌐"
    return None


def _plain_chunks(value: str) -> list[str]:
    return [value[index : index + MAX_DESCRIPTION_LENGTH] for index in range(0, len(value), MAX_DESCRIPTION_LENGTH)] or [""]


def _safe_content_lines(line: str) -> list[str]:
    """Split long text without splitting a Discord Markdown link."""
    if len(line) <= MAX_DESCRIPTION_LENGTH:
        return [line]

    pieces: list[str] = []
    cursor = 0
    for match in re.finditer(r"\[[^\]\n]+\]\(https?://[^\s)]+\)", line):
        if match.start() > cursor:
            pieces.extend(_plain_chunks(line[cursor : match.start()]))
        link = match.group()
        if len(link) <= MAX_DESCRIPTION_LENGTH:
            pieces.append(link)
        else:
            label = re.sub(r"^\[([^\]]+)\]\(.*$", r"\1", link)
            href = re.sub(r"^\[[^\]]+\]\((.*)\)$", r"\1", link)
            pieces.extend(_plain_chunks(label))
            pieces.extend(_plain_chunks(href))
        cursor = match.end()
    if cursor < len(line):
        pieces.extend(_plain_chunks(line[cursor:]))
    return pieces or [""]


def _overflow_notice(description: str, notice: str) -> str:
    """Make room for a notice by dropping whole trailing lines, never link fragments."""
    suffix = f"\n\n{notice}"
    kept = description.rstrip()
    while len(kept) + len(suffix) > MAX_DESCRIPTION_LENGTH and "\n" in kept:
        kept = kept.rsplit("\n", 1)[0].rstrip()
    if len(kept) + len(suffix) <= MAX_DESCRIPTION_LENGTH:
        return f"{kept}{suffix}"
    return notice


def format_content_descriptions(
    announcement: Announcement,
    content: str,
    *,
    max_embeds: int = MAX_CONTENT_EMBEDS,
) -> list[str]:
    """Build Discord-safe descriptions, preferring paragraph boundaries and whole links."""
    prefix = (
        f"🏷️ 公告分類：{_normalize_category(announcement.category)}　　"
        f"📅 公告日期：{announcement.date}\n\n📄 **公告內容**\n"
    )
    descriptions = [prefix]
    source_lines = _format_announcement_content(content).split("\n")
    for source_index, source_line in enumerate(source_lines):
        safe_lines = _safe_content_lines(source_line)
        for line_index, line in enumerate(safe_lines):
            has_following_line = (
                line_index < len(safe_lines) - 1 or source_index < len(source_lines) - 1
            )
            piece = line + ("\n" if has_following_line and len(line) < MAX_DESCRIPTION_LENGTH else "")
            if len(descriptions[-1]) + len(piece) <= MAX_DESCRIPTION_LENGTH:
                descriptions[-1] += piece
            elif len(descriptions) < max_embeds:
                descriptions.append(piece)
            else:
                descriptions[-1] = _overflow_notice(
                    descriptions[-1], "……內容過長，請點擊公告標題查看完整原文。"
                )
                return descriptions
    return descriptions


def _public_image_urls(images: Sequence[str] | None) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in images or ():
        parsed = urlsplit(str(value).strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        url = parsed.geturl()
        if url not in seen:
            seen.add(url)
            result.append(url)
    return result


def _logical_text_units(value: str) -> list[tuple[str, bool]]:
    """Return lossless line units, keeping a table bullet with its continuation lines."""
    lines = value.splitlines(keepends=True)
    units: list[tuple[str, bool]] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("• "):
            row = line
            index += 1
            while index < len(lines) and lines[index].startswith("  "):
                row += lines[index]
                index += 1
            units.append((row, True))
            continue
        units.append((line, False))
        index += 1
    return units


def _safe_split_point(value: str, limit: int) -> int:
    point = min(limit, len(value))
    for match in _MARKDOWN_LINK_RE.finditer(value):
        if match.start() < point < match.end():
            if match.start() == 0:
                raise DiscordSendError(
                    "單一 Markdown 連結超過 Discord description 的安全分段長度。"
                )
            point = match.start()
            break

    lower_bound = max(1, point // 2)
    boundaries = [
        value.rfind(marker, lower_bound, point)
        for marker in ("\n", "。", "！", "？", "；", "，", " ")
    ]
    boundary = max(boundaries, default=-1)
    if boundary >= lower_bound:
        point = boundary + 1

    if (
        0 < point < len(value)
        and 0xD800 <= ord(value[point - 1]) <= 0xDBFF
        and 0xDC00 <= ord(value[point]) <= 0xDFFF
    ):
        point -= 1
    if point <= 0:
        raise DiscordSendError("公告正文無法在安全字元邊界分段。")
    return point


def _split_text_unit(value: str, *, atomic: bool) -> list[str]:
    if len(value) <= TARGET_DESCRIPTION_BODY_LENGTH:
        return [value]
    if atomic:
        raise DiscordSendError(
            "單一表格資料列超過 Discord description 的安全分段長度。"
        )

    chunks: list[str] = []
    remaining = value
    while len(remaining) > TARGET_DESCRIPTION_BODY_LENGTH:
        point = _safe_split_point(remaining, TARGET_DESCRIPTION_BODY_LENGTH)
        chunks.append(remaining[:point])
        remaining = remaining[point:]
    if remaining:
        chunks.append(remaining)
    return chunks


def _formatted_text_entries(value: str) -> list[tuple[str, str]]:
    formatted = _format_announcement_content(value)
    if not formatted:
        return []

    entries: list[tuple[str, str]] = []
    current = ""
    for unit, atomic in _logical_text_units(formatted):
        pieces = _split_text_unit(unit, atomic=atomic)
        for piece in pieces:
            if current and len(current) + len(piece) > TARGET_DESCRIPTION_BODY_LENGTH:
                entries.append(("text", current))
                current = ""
            current += piece
    if current:
        entries.append(("text", current))
    return entries


def _ordered_content_entries(
    *,
    content: str | None,
    images: Sequence[str] | None,
    blocks: Sequence[AnnouncementContentBlock] | None,
) -> tuple[list[tuple[str, str]], bool]:
    entries: list[tuple[str, str]] = []
    if blocks is not None:
        for block in blocks:
            if isinstance(block, TextBlock):
                entries.extend(_formatted_text_entries(block.text))
            elif isinstance(block, ImageBlock):
                image_urls = _public_image_urls((block.url,))
                if image_urls:
                    entries.append(("image", image_urls[0]))
    else:
        if content and content.strip():
            entries.extend(_formatted_text_entries(content))
        entries.extend(
            ("image", image_url) for image_url in _public_image_urls(images)
        )
    return entries, bool(entries)


def _partition_content_entries(
    entries: Sequence[tuple[str, str]],
) -> list[list[tuple[str, str]]]:
    if not entries:
        return [[]]

    pages: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] = []
    current_text_length = 0
    for kind, value in entries:
        added_text_length = len(value) if kind == "text" else 0
        exceeds_text_budget = (
            current
            and current_text_length + added_text_length
            > TARGET_MESSAGE_BODY_TEXT_LENGTH
        )
        exceeds_embed_budget = (
            current and len(current) >= MAX_RAW_ENTRIES_PER_MESSAGE
        )
        if exceeds_text_budget or exceeds_embed_budget:
            pages.append(current)
            current = []
            current_text_length = 0
        current.append((kind, value))
        current_text_length += added_text_length
    if current:
        pages.append(current)
    return pages


def _footer_text(announcement: Announcement, *, history_mode: bool) -> str:
    lines = ["Maple Classic Discord Center｜羽田製作"]
    if history_mode:
        lines.append("🏞️ 歷史公告")
    lines.append(f"公告 ID：{announcement.announcement_id}")
    return "\n".join(lines)


def _payload_heading(
    announcement: Announcement,
    *,
    chunk_index: int,
    total_chunks: int,
    has_body: bool,
) -> str:
    info_line = (
        f"🏷️ 公告分類：{_normalize_category(announcement.category)}　　"
        f"📅 公告日期：{announcement.date}"
    )
    if not has_body:
        return f"{info_line}\n\n" if chunk_index == 1 else ""
    content_heading = (
        "📄 **公告內容**"
        if total_chunks == 1
        else f"📄 **公告內容（{chunk_index}/{total_chunks}）**"
    )
    if chunk_index == 1:
        return f"{info_line}\n\n{content_heading}\n"
    return f"{content_heading}\n"


def _embed_text_length(embed: dict[str, object]) -> int:
    total = 0
    for key in ("title", "description"):
        value = embed.get(key)
        if isinstance(value, str):
            total += len(value)
    author = embed.get("author")
    if isinstance(author, dict) and isinstance(author.get("name"), str):
        total += len(author["name"])
    footer = embed.get("footer")
    if isinstance(footer, dict) and isinstance(footer.get("text"), str):
        total += len(footer["text"])
    fields = embed.get("fields")
    if isinstance(fields, list):
        for field in fields:
            if not isinstance(field, dict):
                continue
            for key in ("name", "value"):
                value = field.get(key)
                if isinstance(value, str):
                    total += len(value)
    return total


def _payload_limit_error(
    announcement: Announcement,
    *,
    chunk_index: int,
    total_chunks: int,
    reason: str,
) -> DiscordSendError:
    return DiscordSendError(
        "Discord payload 發送前驗證失敗："
        f"ID={announcement.announcement_id} "
        f"chunk={chunk_index}/{total_chunks} reason={reason}"
    )


def validate_announcement_payloads(
    announcement: Announcement,
    payloads: Sequence[dict[str, object]],
) -> None:
    if not payloads:
        raise _payload_limit_error(
            announcement,
            chunk_index=0,
            total_chunks=0,
            reason="沒有可發送的 webhook payload",
        )

    total_chunks = len(payloads)
    for chunk_index, payload in enumerate(payloads, start=1):
        if not isinstance(payload.get("username"), str):
            raise _payload_limit_error(
                announcement,
                chunk_index=chunk_index,
                total_chunks=total_chunks,
                reason="username 結構不正確",
            )
        allowed_mentions = payload.get("allowed_mentions")
        if not isinstance(allowed_mentions, dict) or allowed_mentions.get("parse") != []:
            raise _payload_limit_error(
                announcement,
                chunk_index=chunk_index,
                total_chunks=total_chunks,
                reason="allowed_mentions 結構不正確",
            )
        embeds = payload.get("embeds")
        if not isinstance(embeds, list) or not embeds or len(embeds) > MAX_CONTENT_EMBEDS:
            embed_count = len(embeds) if isinstance(embeds, list) else 0
            raise _payload_limit_error(
                announcement,
                chunk_index=chunk_index,
                total_chunks=total_chunks,
                reason=f"Embed 數量={embed_count}，上限={MAX_CONTENT_EMBEDS}",
            )

        for embed_index, embed in enumerate(embeds, start=1):
            if not isinstance(embed, dict):
                raise _payload_limit_error(
                    announcement,
                    chunk_index=chunk_index,
                    total_chunks=total_chunks,
                    reason=f"Embed {embed_index} 結構不正確",
                )
            title = embed.get("title")
            if title is not None and not isinstance(title, str):
                raise _payload_limit_error(
                    announcement,
                    chunk_index=chunk_index,
                    total_chunks=total_chunks,
                    reason=f"Embed {embed_index} title 結構不正確",
                )
            if isinstance(title, str) and len(title) > MAX_EMBED_TITLE_LENGTH:
                raise _payload_limit_error(
                    announcement,
                    chunk_index=chunk_index,
                    total_chunks=total_chunks,
                    reason=f"Embed {embed_index} title 長度={len(title)}",
                )
            description = embed.get("description")
            if description is not None and not isinstance(description, str):
                raise _payload_limit_error(
                    announcement,
                    chunk_index=chunk_index,
                    total_chunks=total_chunks,
                    reason=f"Embed {embed_index} description 結構不正確",
                )
            if isinstance(description, str) and len(description) > MAX_DESCRIPTION_LENGTH:
                raise _payload_limit_error(
                    announcement,
                    chunk_index=chunk_index,
                    total_chunks=total_chunks,
                    reason=f"Embed {embed_index} description 長度={len(description)}",
                )
            author = embed.get("author")
            if author is not None and not isinstance(author, dict):
                raise _payload_limit_error(
                    announcement,
                    chunk_index=chunk_index,
                    total_chunks=total_chunks,
                    reason=f"Embed {embed_index} author 結構不正確",
                )
            if isinstance(author, dict):
                author_name = author.get("name")
                if not isinstance(author_name, str):
                    raise _payload_limit_error(
                        announcement,
                        chunk_index=chunk_index,
                        total_chunks=total_chunks,
                        reason=f"Embed {embed_index} author name 結構不正確",
                    )
                if len(author_name) > MAX_EMBED_AUTHOR_LENGTH:
                    raise _payload_limit_error(
                        announcement,
                        chunk_index=chunk_index,
                        total_chunks=total_chunks,
                        reason=f"Embed {embed_index} author 長度={len(author_name)}",
                    )
            footer = embed.get("footer")
            if footer is not None and not isinstance(footer, dict):
                raise _payload_limit_error(
                    announcement,
                    chunk_index=chunk_index,
                    total_chunks=total_chunks,
                    reason=f"Embed {embed_index} footer 結構不正確",
                )
            if isinstance(footer, dict):
                footer_text = footer.get("text")
                if not isinstance(footer_text, str):
                    raise _payload_limit_error(
                        announcement,
                        chunk_index=chunk_index,
                        total_chunks=total_chunks,
                        reason=f"Embed {embed_index} footer text 結構不正確",
                    )
                if len(footer_text) > MAX_EMBED_FOOTER_LENGTH:
                    raise _payload_limit_error(
                        announcement,
                        chunk_index=chunk_index,
                        total_chunks=total_chunks,
                        reason=f"Embed {embed_index} footer 長度={len(footer_text)}",
                    )
            image = embed.get("image")
            if image is not None and (
                not isinstance(image, dict)
                or not isinstance(image.get("url"), str)
            ):
                raise _payload_limit_error(
                    announcement,
                    chunk_index=chunk_index,
                    total_chunks=total_chunks,
                    reason=f"Embed {embed_index} image 結構不正確",
                )
            if title is None and description is None and image is None:
                raise _payload_limit_error(
                    announcement,
                    chunk_index=chunk_index,
                    total_chunks=total_chunks,
                    reason=f"Embed {embed_index} 沒有可發送內容",
                )
            fields = embed.get("fields")
            if fields is not None:
                if not isinstance(fields, list) or len(fields) > MAX_EMBED_FIELDS:
                    raise _payload_limit_error(
                        announcement,
                        chunk_index=chunk_index,
                        total_chunks=total_chunks,
                        reason=f"Embed {embed_index} fields 結構或數量不正確",
                    )
                for field_index, field in enumerate(fields, start=1):
                    if not isinstance(field, dict):
                        raise _payload_limit_error(
                            announcement,
                            chunk_index=chunk_index,
                            total_chunks=total_chunks,
                            reason=f"Embed {embed_index} field {field_index} 結構不正確",
                        )
                    name = field.get("name")
                    value = field.get("value")
                    if not isinstance(name, str) or len(name) > MAX_EMBED_FIELD_NAME_LENGTH:
                        raise _payload_limit_error(
                            announcement,
                            chunk_index=chunk_index,
                            total_chunks=total_chunks,
                            reason=f"Embed {embed_index} field {field_index} name 超限",
                        )
                    if not isinstance(value, str) or len(value) > MAX_EMBED_FIELD_VALUE_LENGTH:
                        raise _payload_limit_error(
                            announcement,
                            chunk_index=chunk_index,
                            total_chunks=total_chunks,
                            reason=f"Embed {embed_index} field {field_index} value 超限",
                        )

        message_text_length = sum(_embed_text_length(embed) for embed in embeds)
        if message_text_length > MAX_MESSAGE_EMBED_TEXT_LENGTH:
            raise _payload_limit_error(
                announcement,
                chunk_index=chunk_index,
                total_chunks=total_chunks,
                reason=(
                    f"Embed 總文字長度={message_text_length}，"
                    f"上限={MAX_MESSAGE_EMBED_TEXT_LENGTH}"
                ),
            )


def build_announcement_payloads(
    announcement: Announcement,
    *,
    thumbnail_url: str | None = None,
    content: str | None = None,
    images: Sequence[str] | None = None,
    blocks: Sequence[AnnouncementContentBlock] | None = None,
    history_mode: bool = False,
) -> list[dict[str, object]]:
    """Build and preflight every webhook payload before the first network request."""
    try:
        thumbnail_url = validate_https_image_url(thumbnail_url)
    except ValueError as exc:
        raise DiscordSendError(str(exc)) from exc

    _validate_content_safety(announcement, content=content, blocks=blocks)
    entries, has_body = _ordered_content_entries(
        content=content,
        images=images,
        blocks=blocks,
    )
    pages = _partition_content_entries(entries)
    total_chunks = len(pages)
    payloads: list[dict[str, object]] = []
    category_color = get_category_color(announcement.category)

    for chunk_index, page in enumerate(pages, start=1):
        page_entries = list(page)
        heading = _payload_heading(
            announcement,
            chunk_index=chunk_index,
            total_chunks=total_chunks,
            has_body=has_body,
        )
        if page_entries and page_entries[0][0] == "text":
            first_kind, first_value = page_entries[0]
            page_entries[0] = (first_kind, f"{heading}{first_value}")
        else:
            page_entries.insert(0, ("text", heading))

        embeds: list[dict[str, object]] = []
        for kind, value in page_entries:
            if kind == "image":
                embeds.append({"image": {"url": value}})
            else:
                embeds.append({"color": category_color, "description": value})

        if chunk_index == 1:
            first_embed = embeds[0]
            first_embed["author"] = {
                "name": _truncate("新楓之谷：經典版官方消息", MAX_EMBED_AUTHOR_LENGTH)
            }
            first_embed["title"] = get_embed_title(
                announcement.category, announcement.title
            )
            first_embed["url"] = announcement.url
            if thumbnail_url:
                first_embed["thumbnail"] = {"url": thumbnail_url}
                first_embed["author"]["icon_url"] = thumbnail_url

        if chunk_index == total_chunks:
            if "image" in embeds[-1]:
                embeds.append({"color": category_color, "description": "\u200b"})
            embeds[-1]["footer"] = {
                "text": _footer_text(announcement, history_mode=history_mode)
            }
            if thumbnail_url:
                embeds[-1]["footer"]["icon_url"] = thumbnail_url

        payloads.append(
            {
                "username": "Maple Classic Bot",
                "embeds": embeds,
                "allowed_mentions": {"parse": []},
            }
        )

    validate_announcement_payloads(announcement, payloads)
    return payloads


def _validate_content_safety(
    announcement: Announcement,
    *,
    content: str | None,
    blocks: Sequence[AnnouncementContentBlock] | None,
) -> None:
    source_text = content or ""
    if blocks is not None:
        source_text = "\n".join(
            block.text for block in blocks if isinstance(block, TextBlock)
        )
    markers = template_garbage_markers(source_text)
    if len(markers) >= 2:
        raise DiscordSendError(
            "公告內容安全檢查失敗："
            f"ID={announcement.announcement_id} "
            f"title={announcement.title} "
            f"reason=疑似完整網站模板（{', '.join(markers)}）"
        )


def _format_text_block(announcement: Announcement, text: str, *, include_header: bool) -> list[str]:
    prefix = (
        f"🏷️ 公告分類：{_normalize_category(announcement.category)}　　"
        f"📅 公告日期：{announcement.date}\n\n📄 **公告內容**\n"
        if include_header
        else ""
    )
    descriptions = [prefix]
    source_lines = _format_announcement_content(text).split("\n")
    for source_index, source_line in enumerate(source_lines):
        for line_index, line in enumerate(_safe_content_lines(source_line)):
            has_next = line_index < len(_safe_content_lines(source_line)) - 1 or source_index < len(source_lines) - 1
            piece = line + ("\n" if has_next and len(line) < MAX_DESCRIPTION_LENGTH else "")
            if len(descriptions[-1]) + len(piece) <= MAX_DESCRIPTION_LENGTH:
                descriptions[-1] += piece
            else:
                descriptions.append(piece)
    return [description for description in descriptions if description.strip()]


def format_content_entries(
    announcement: Announcement,
    blocks: Sequence[AnnouncementContentBlock],
) -> list[tuple[str, str]]:
    """Translate ordered parser blocks into ordered text/image embed entries."""
    entries: list[tuple[str, str]] = []
    header_added = False
    for block in blocks:
        if isinstance(block, TextBlock):
            descriptions = _format_text_block(announcement, block.text, include_header=not header_added)
            if descriptions:
                entries.extend(("text", description) for description in descriptions)
                header_added = True
        elif isinstance(block, ImageBlock):
            image_urls = _public_image_urls((block.url,))
            if not image_urls:
                continue
            if not header_added:
                entries.append(("text", _format_text_block(announcement, "", include_header=True)[0]))
                header_added = True
            entries.append(("image", image_urls[0]))
    if not header_added:
        entries.append(("text", _format_text_block(announcement, "", include_header=True)[0]))
    return entries
def _normalize_category(category: str | None) -> str:
    """Return a category in its canonical form for display and lookup."""
    return (category or "").strip()


def get_category_color(category: str | None) -> int:
    """Return the Discord embed color for an announcement category."""
    return CATEGORY_COLORS.get(_normalize_category(category), DEFAULT_CATEGORY_COLOR)


def get_category_icon(category: str | None) -> str:
    """Return the icon for an announcement category."""
    return CATEGORY_ICONS.get(_normalize_category(category), DEFAULT_CATEGORY_ICON)


def get_category_display(category: str | None) -> str:
    """Return the category label prefixed with its Discord-friendly icon."""
    normalized_category = _normalize_category(category)
    if not normalized_category:
        return get_category_icon(category)
    return f"{get_category_icon(category)} {normalized_category}"


def _truncate(value: object, limit: int) -> str:
    text = str(value)
    if len(text) <= limit:
        return text
    if limit == 1:
        return "…"
    return f"{text[: limit - 1]}…"


def get_embed_title(category: str | None, title: object) -> str:
    """Return an icon-prefixed embed title within Discord's 256-character limit."""
    prefix = f"{get_category_icon(category)} "
    return f"{prefix}{_truncate(title, 256 - len(prefix))}"


def format_description(announcement: Announcement) -> str:
    return _truncate(
        f"🏷️ 公告分類：{_normalize_category(announcement.category)}　　"
        f"📅 公告日期：{announcement.date}\n\n", 4096,
    )


def format_content_description(announcement: Announcement, content: str) -> str:
    """Return the first content description for compatibility with existing callers."""
    return format_content_descriptions(announcement, content)[0]

def _validate_webhook_url(webhook_url: str) -> None:
    try:
        parsed = urlsplit(webhook_url)
    except (TypeError, ValueError) as exc:
        raise DiscordSendError("Discord Webhook URL 格式不正確。") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname not in DISCORD_WEBHOOK_HOSTS
        or not parsed.path.startswith("/api/webhooks/")
        or not parsed.path.removeprefix("/api/webhooks/").strip("/")
    ):
        raise DiscordSendError("Discord Webhook URL 格式不正確。")


def _response_detail(response: object, *sensitive_values: str) -> str:
    status_code = getattr(response, "status_code", None)
    text = str(getattr(response, "text", ""))
    for value in sensitive_values:
        if value:
            text = text.replace(value, "[REDACTED]")
    text = text[:200]
    if status_code is None:
        return ""
    return f"（HTTP {status_code}: {text}）"


def _retry_after(response: object, fallback: float) -> float:
    try:
        value = float(response.json().get("retry_after"))
        if value >= 0:
            return min(value, MAX_RETRY_AFTER_SECONDS)
    except (AttributeError, TypeError, ValueError, requests.RequestException):
        pass
    return min(fallback, MAX_RETRY_AFTER_SECONDS)


def _discord_message_id(response: object) -> str | None:
    try:
        payload = response.json()
    except (AttributeError, TypeError, ValueError, requests.RequestException):
        return None
    if not isinstance(payload, dict):
        return None
    message_id = payload.get("id")
    return str(message_id) if message_id is not None else None


def _discord_channel_id(response: object) -> str | None:
    try:
        payload = response.json()
    except (AttributeError, TypeError, ValueError, requests.RequestException):
        return None
    if not isinstance(payload, dict):
        return None
    channel_id = payload.get("channel_id")
    return str(channel_id) if channel_id is not None else None


def _send_payload(
    client: object,
    *,
    webhook_url: str,
    payload: dict[str, object],
    user_agent: str,
    timeout: float,
    sleep: Callable[[float], None],
) -> tuple[str | None, str | None]:
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = client.post(
                webhook_url,
                params={"wait": "true"},
                json=payload,
                headers={"User-Agent": user_agent},
                timeout=timeout,
            )
            status_code = getattr(response, "status_code", None)
            if status_code is None:
                response.raise_for_status()
                return (
                    _discord_message_id(response),
                    _discord_channel_id(response),
                )
            if 200 <= status_code < 300:
                return (
                    _discord_message_id(response),
                    _discord_channel_id(response),
                )
            retryable = status_code == 429 or status_code in {500, 502, 503, 504}
            if not retryable:
                raise DiscordSendError(
                    f"Discord Webhook 發送失敗：{_response_detail(response, webhook_url)}"
                )
            error = DiscordSendError(
                f"Discord Webhook 發送失敗：{_response_detail(response, webhook_url)}"
            )
            delay = (
                _retry_after(response, float(2 ** (attempt - 1)))
                if status_code == 429
                else float(2 ** (attempt - 1))
            )
        except (requests.ConnectionError, requests.Timeout) as exc:
            error = DiscordSendError("Discord Webhook 發送失敗：連線或 timeout 錯誤。")
            delay = float(2 ** (attempt - 1))
        except requests.RequestException as exc:
            response = getattr(exc, "response", None)
            raise DiscordSendError(
                f"Discord Webhook 發送失敗：請求錯誤{_response_detail(response, webhook_url)}"
            ) from exc

        if attempt == MAX_ATTEMPTS:
            raise error
        sleep(min(delay, MAX_RETRY_AFTER_SECONDS))
    raise DiscordSendError("Discord Webhook 發送失敗：已超過重試次數。")


def _publish_message(
    client: object,
    *,
    channel_id: str,
    message_id: str,
    bot_token: str,
    webhook_url: str,
    user_agent: str,
    timeout: float,
    sleep: Callable[[float], None],
) -> None:
    if not channel_id.isdecimal() or not message_id.isdecimal():
        raise DiscordSendError(
            "Discord Webhook 回應的頻道或訊息 ID 格式不正確。"
        )

    publish_url = (
        f"{DISCORD_API_BASE_URL}/channels/{channel_id}/messages/{message_id}/crosspost"
    )
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = client.post(
                publish_url,
                headers={
                    "Authorization": f"Bot {bot_token}",
                    "User-Agent": user_agent,
                },
                timeout=timeout,
            )
            status_code = getattr(response, "status_code", None)
            if status_code is None:
                response.raise_for_status()
                return
            if 200 <= status_code < 300:
                return
            retryable = status_code == 429 or status_code in {500, 502, 503, 504}
            if not retryable:
                raise DiscordSendError(
                    "Discord 公告發布失敗："
                    f"{_response_detail(response, webhook_url, bot_token)}"
                )
            error = DiscordSendError(
                "Discord 公告發布失敗："
                f"{_response_detail(response, webhook_url, bot_token)}"
            )
            delay = (
                _retry_after(response, float(2 ** (attempt - 1)))
                if status_code == 429
                else float(2 ** (attempt - 1))
            )
        except (requests.ConnectionError, requests.Timeout):
            error = DiscordSendError(
                "Discord 公告發布失敗：連線或 timeout 錯誤。"
            )
            delay = float(2 ** (attempt - 1))
        except requests.RequestException as exc:
            response = getattr(exc, "response", None)
            raise DiscordSendError(
                "Discord 公告發布失敗：請求錯誤"
                f"{_response_detail(response, webhook_url, bot_token)}"
            ) from exc

        if attempt == MAX_ATTEMPTS:
            raise error
        sleep(min(delay, MAX_RETRY_AFTER_SECONDS))
    raise DiscordSendError("Discord 公告發布失敗：已超過重試次數。")


def send_announcement(
    webhook_url: str,
    announcement: Announcement,
    *,
    timeout: float = 15,
    user_agent: str,
    thumbnail_url: str | None = None,
    spacer_emoji: str | None = None,
    content: str | None = None,
    images: Sequence[str] | None = None,
    blocks: Sequence[AnnouncementContentBlock] | None = None,
    history_mode: bool = False,
    bot_token: str | None = None,
    session: requests.Session | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    if not webhook_url:
        raise DiscordSendError("缺少 DISCORD_WEBHOOK_URL，無法發送公告。")
    _validate_webhook_url(webhook_url)
    try:
        validate_discord_spacer_emoji(spacer_emoji)
    except ValueError as exc:
        raise DiscordSendError(str(exc)) from exc

    payloads = build_announcement_payloads(
        announcement,
        thumbnail_url=thumbnail_url,
        content=content,
        images=images,
        blocks=blocks,
        history_mode=history_mode,
    )
    validate_announcement_payloads(announcement, payloads)
    client = session or requests.Session()
    owns_session = session is None
    try:
        total_chunks = len(payloads)
        for chunk_index, payload in enumerate(payloads, start=1):
            embeds = payload["embeds"]
            text_length = sum(_embed_text_length(embed) for embed in embeds)
            LOGGER.info(
                "Sending Discord chunk: ID=%s chunk=%d/%d embeds=%d text_length=%d",
                announcement.announcement_id,
                chunk_index,
                total_chunks,
                len(embeds),
                text_length,
            )
            try:
                message_id, channel_id = _send_payload(
                    client,
                    webhook_url=webhook_url,
                    payload=payload,
                    user_agent=user_agent,
                    timeout=timeout,
                    sleep=sleep,
                )
            except DiscordSendError as exc:
                LOGGER.error(
                    "Discord chunk failed: ID=%s chunk=%d/%d reason=%s",
                    announcement.announcement_id,
                    chunk_index,
                    total_chunks,
                    exc,
                )
                raise DiscordSendError(
                    "Discord Webhook 分段發送失敗："
                    f"ID={announcement.announcement_id} "
                    f"chunk={chunk_index}/{total_chunks} {exc}"
                ) from exc
            LOGGER.info(
                "Discord chunk success: ID=%s chunk=%d/%d message_id=%s",
                announcement.announcement_id,
                chunk_index,
                total_chunks,
                message_id or "unavailable",
            )
            publish_token = (bot_token or "").strip()
            if not publish_token:
                continue
            if not message_id or not channel_id:
                raise DiscordSendError(
                    "Discord 公告分段發布失敗："
                    f"ID={announcement.announcement_id} "
                    f"chunk={chunk_index}/{total_chunks} "
                    "Webhook 回應缺少 message_id 或 channel_id。"
                )
            LOGGER.info(
                "Publishing Discord chunk: ID=%s chunk=%d/%d message_id=%s",
                announcement.announcement_id,
                chunk_index,
                total_chunks,
                message_id,
            )
            try:
                _publish_message(
                    client,
                    channel_id=channel_id,
                    message_id=message_id,
                    bot_token=publish_token,
                    webhook_url=webhook_url,
                    user_agent=user_agent,
                    timeout=timeout,
                    sleep=sleep,
                )
            except DiscordSendError as exc:
                LOGGER.error(
                    "Discord chunk publish failed: ID=%s chunk=%d/%d reason=%s",
                    announcement.announcement_id,
                    chunk_index,
                    total_chunks,
                    exc,
                )
                raise DiscordSendError(
                    "Discord 公告分段發布失敗："
                    f"ID={announcement.announcement_id} "
                    f"chunk={chunk_index}/{total_chunks} {exc}"
                ) from exc
            LOGGER.info(
                "Discord chunk publish success: ID=%s chunk=%d/%d message_id=%s",
                announcement.announcement_id,
                chunk_index,
                total_chunks,
                message_id,
            )
    finally:
        if owns_session:
            client.close()
