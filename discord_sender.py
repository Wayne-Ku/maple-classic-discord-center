"""Send announcement embeds through a Discord webhook."""

from __future__ import annotations

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


def _response_detail(response: object, webhook_url: str) -> str:
    status_code = getattr(response, "status_code", None)
    text = str(getattr(response, "text", "")).replace(webhook_url, "[REDACTED]")[:200]
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
    session: requests.Session | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    if not webhook_url:
        raise DiscordSendError("缺少 DISCORD_WEBHOOK_URL，無法發送公告。")
    _validate_webhook_url(webhook_url)
    try:
        thumbnail_url = validate_https_image_url(thumbnail_url)
    except ValueError as exc:
        raise DiscordSendError(str(exc)) from exc
    try:
        validate_discord_spacer_emoji(spacer_emoji)
    except ValueError as exc:
        raise DiscordSendError(str(exc)) from exc

    _validate_content_safety(
        announcement,
        content=content,
        blocks=blocks,
    )

    if blocks is not None:
        entries = format_content_entries(announcement, blocks)
    else:
        descriptions = (
            format_content_descriptions(announcement, content)
            if content and content.strip()
            else [format_description(announcement)]
        )
        entries = [("text", description) for description in descriptions]
        entries.extend(("image", image_url) for image_url in _public_image_urls(images))

    if len(entries) > MAX_CONTENT_EMBEDS:
        entries = entries[: MAX_CONTENT_EMBEDS - 1] + [
            ("text", "內容過長，其餘內容與圖片請至公告原文查看")
        ]
    elif entries[-1][0] == "image":
        if len(entries) == MAX_CONTENT_EMBEDS:
            entries[-1] = ("text", "內容過長，其餘內容與圖片請至公告原文查看")
        else:
            entries.append(("text", "\u200b"))

    footer_lines = ["Maple Classic Discord Center｜羽田製作"]
    if history_mode:
        footer_lines.append("🏞️ 歷史公告")
    footer_lines.append(f"公告 ID：{announcement.announcement_id}")
    first_kind, first_value = entries[0]
    if first_kind != "text":
        raise DiscordSendError("公告內容缺少可用文字區塊")
    embed = {
        "author": {"name": _truncate("新楓之谷：經典版官方消息", 256)},
        "title": get_embed_title(announcement.category, announcement.title),
        "url": announcement.url,
        "color": get_category_color(announcement.category),
        "description": first_value,
    }
    if thumbnail_url:
        embed["thumbnail"] = {"url": thumbnail_url}
        embed["author"]["icon_url"] = thumbnail_url

    embeds = [embed]
    for kind, value in entries[1:]:
        if kind == "image":
            embeds.append({"image": {"url": value}})
            continue
        embeds.append(
            {
                "color": get_category_color(announcement.category),
                "description": value,
            }
        )
    embeds[-1]["footer"] = {"text": _truncate("\n".join(footer_lines), 2048)}
    if thumbnail_url:
        embeds[-1]["footer"]["icon_url"] = thumbnail_url
    payload = {
        "username": "Maple Classic Bot",
        "embeds": embeds,
        "allowed_mentions": {"parse": []},
    }
    client = session or requests.Session()
    owns_session = session is None
    try:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = client.post(
                    webhook_url,
                    json=payload,
                    headers={"User-Agent": user_agent},
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
                        f"Discord Webhook 發送失敗：{_response_detail(response, webhook_url)}"
                    )
                error = DiscordSendError(
                    f"Discord Webhook 發送失敗：{_response_detail(response, webhook_url)}"
                )
                delay = _retry_after(response, float(2 ** (attempt - 1))) if status_code == 429 else float(2 ** (attempt - 1))
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
    finally:
        if owns_session:
            client.close()
