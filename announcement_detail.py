"""Retrieve ordered text and image blocks from official announcements."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urljoin, urlsplit

import requests
from bs4 import BeautifulSoup, NavigableString, Tag

from maple_parser import Announcement

DETAIL_API_URL = "https://maplestoryclassic.beanfun.com/api/Bulletin/BulletinDetail"
LEGACY_NEWS_API_URL = "https://gamaapi.beanfun.com/Api/News/GetNewsContent"
MAX_PLAIN_TEXT_LENGTH = 500_000
LOGGER = logging.getLogger("maple-classic-discord-center")

_NON_BODY_TEXT = frozenset({"新楓之谷：經典版官方網站", "新楓之谷：經典版", "MapleStory Classic Official Website"})
_HTML_CONTENT_SELECTORS = (
    ".bulletin-detail__content",
    ".bulletin-content",
    ".announcement-content",
    ".content-detail",
    ".news-content",
    ".NewsPageContent",
)
_IMAGE_NOISE_MARKERS = ("spacer", "tracking", "pixel", "transparent", "blank", "logo", "icon", "button", "btn", "top", "download")
_BLOCK_TAGS = frozenset({"p", "div", "li", "ul", "ol", "table", "tr", "section", "article", "h2", "h3", "h4", "td", "th"})
_TEMPLATE_GARBAGE_SIGNATURES = (
    "doctype",
    "xhtml 1.0 transitional",
    "w3c//dtd",
    "start search bar",
    "end search bar",
    "start google ad",
    "end google ad",
    "begin: pagination",
    "end: pagination",
    "comscore",
    "<body",
    "<html",
)
_WRAPPED_MARKDOWN_LINK_RE = re.compile(
    r"(?m)^[ \t]*[【\[][ \t]*(?:\n[ \t]*)?"
    r"(?P<link>\[[^\]\n]+\]\(https?://[^\s)]+\))[ \t]*(?:\n[ \t]*)?"
    r"[】\]][ \t]*$"
)
_TABLE_CONTINUATION_TOKEN = "\uf000"
_TABLE_COLUMN_NAMES = ("一", "二", "三", "四", "五", "六", "七", "八", "九", "十")
_SANCTION_TITLE_MARKER = "遊戲異常行為制裁公告"
_SANCTION_BODY_MARKER = "以下帳號因"
_SANCTION_TABLE_HEADERS = frozenset({"角色名稱", "制裁結果"})


class AnnouncementDetailError(RuntimeError):
    """Raised when an announcement body cannot be obtained."""


@dataclass(frozen=True)
class TextBlock:
    text: str


@dataclass(frozen=True)
class ImageBlock:
    url: str


AnnouncementContentBlock = TextBlock | ImageBlock


@dataclass(frozen=True)
class AnnouncementDetail:
    """Ordered official content. Legacy projections are retained for callers."""

    plain_text: str
    links: tuple[str, ...] = ()
    images: tuple[str, ...] = ()
    blocks: tuple[AnnouncementContentBlock, ...] = ()

    def __post_init__(self) -> None:
        if not self.blocks:
            blocks: list[AnnouncementContentBlock] = []
            if self.plain_text:
                blocks.append(TextBlock(self.plain_text))
            blocks.extend(ImageBlock(url) for url in self.images)
            object.__setattr__(self, "blocks", tuple(blocks))


def _clean_text(value: str) -> str:
    text = value.replace("\xa0", " ").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"(?<=\d)[ \t]+~[ \t]+(?=\d)", " ～ ", text)
    text = re.sub(r"(?<=\d)~(?=\d)", "～", text)
    text = re.sub(r"(?<=[\u3400-\u9fff])~(?=\s*(?:\n|$))", "～", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = _WRAPPED_MARKDOWN_LINK_RE.sub(r"\g<link>", text)
    text = re.sub(r"\n{3,}", "\n\n", text).replace(_TABLE_CONTINUATION_TOKEN, "  ")
    return text.strip()


def _absolute_http_url(value: object, base_url: str) -> str | None:
    raw = str(value or "").strip()
    if not raw or raw.startswith("data:"):
        return None
    resolved = urljoin(base_url, raw)
    parsed = urlsplit(resolved)
    return resolved if parsed.scheme in {"http", "https"} and parsed.netloc else None


def _discord_link_text(label: str, href: str) -> str:
    """Avoid masked-link syntax when the visible label is already the URL."""
    return href if label.strip() == href else f"[{label or href}]({href})"


def _is_tiny_dimension(value: object) -> bool:
    match = re.search(r"\d+", str(value or ""))
    return bool(match and int(match.group()) <= 1)


def _is_content_image(image: Tag, url: str) -> bool:
    if _is_tiny_dimension(image.get("width")) or _is_tiny_dimension(image.get("height")):
        return False
    metadata = " ".join([url, str(image.get("alt") or ""), str(image.get("title") or ""), " ".join(image.get("class") or ()), str(image.get("id") or "")]).casefold()
    return not any(marker in metadata for marker in _IMAGE_NOISE_MARKERS)


def _direct_table_cells(row: Tag) -> list[Tag]:
    return [cell for cell in row.find_all(("th", "td"), recursive=False)]


def _cell_background(cell: Tag) -> str | None:
    """Return an explicitly configured cell background used by visual headers."""
    bgcolor = str(cell.get("bgcolor") or "").strip().casefold()
    if bgcolor:
        return bgcolor

    style = str(cell.get("style") or "")
    match = re.search(
        r"(?:^|;)\s*background(?:-color)?\s*:\s*([^;]+)",
        style,
        flags=re.IGNORECASE,
    )
    return match.group(1).strip().casefold() if match else None


def _has_visual_header_row(rows: list[tuple[list[Tag], list[str]]]) -> bool:
    """Recognize API tables whose header semantics exist only in cell styling."""
    if len(rows) < 2:
        return False

    first_cells, first_values = rows[0]
    if len(first_cells) < 2 or not all(first_values):
        return False

    header_backgrounds = [_cell_background(cell) for cell in first_cells]
    if not header_backgrounds[0] or len(set(header_backgrounds)) != 1:
        return False

    header_background = header_backgrounds[0]
    return any(
        len(cells) == len(first_cells)
        and any(_cell_background(cell) != header_background for cell in cells)
        for cells, _ in rows[1:]
    )


def _is_sanction_announcement(title: str, html: str) -> bool:
    if _SANCTION_TITLE_MARKER not in title:
        return False
    body_text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    return _SANCTION_BODY_MARKER in re.sub(r"\s+", "", body_text)


def _is_sanction_list_table(table: Tag) -> bool:
    first_row = next(
        (
            row
            for row in table.find_all("tr")
            if row.find_parent("table") is table
        ),
        None,
    )
    if first_row is None:
        return False
    headers = {
        re.sub(r"\s+", "", cell.get_text(" ", strip=True))
        for cell in _direct_table_cells(first_row)
    }
    return _SANCTION_TABLE_HEADERS.issubset(headers)


def _format_table(table: Tag, render_cell: Callable[[Tag], str]) -> str:
    """Render table rows as searchable, mobile-friendly text without losing columns."""
    row_tags = [
        row for row in table.find_all("tr") if row.find_parent("table") is table
    ]
    rows: list[tuple[list[Tag], list[str]]] = []
    for row in row_tags:
        cells = _direct_table_cells(row)
        values = [_clean_text(render_cell(cell)) for cell in cells]
        if cells and any(values):
            rows.append((cells, values))
    if not rows:
        return ""

    first_cells, first_values = rows[0]
    nonempty_first_cells = [
        cell for cell, value in zip(first_cells, first_values, strict=True) if value
    ]
    has_header = (
        any(cell.name == "th" for cell in first_cells)
        or any(cell.find_parent("thead") is not None for cell in first_cells)
        or _has_visual_header_row(rows)
        or (
            len(rows) > 1
            and bool(nonempty_first_cells)
            and all(
                cell.find(("strong", "b")) is not None
                for cell in nonempty_first_cells
            )
        )
    )
    headers = first_values if has_header else [
        f"欄位{_TABLE_COLUMN_NAMES[index]}"
        if index < len(_TABLE_COLUMN_NAMES)
        else f"欄位{index + 1}"
        for index in range(max(len(values) for _, values in rows))
    ]
    data_rows = (
        [values for _, values in rows[1:]]
        if has_header
        else [values for _, values in rows]
    )

    normalized_headers = [re.sub(r"\s+", "", header) for header in headers]
    reward_columns = {
        name: normalized_headers.index(name)
        for name in ("道具名稱", "數量", "期限")
        if name in normalized_headers
    }
    if len(reward_columns) == 3:
        reward_lines: list[str] = []
        for values in data_rows:
            try:
                item_name = values[reward_columns["道具名稱"]]
                quantity = values[reward_columns["數量"]]
                duration = values[reward_columns["期限"]]
            except IndexError:
                continue
            if not any((item_name, quantity, duration)):
                continue
            item_name = re.sub(r"\(([^()\n]+)\)", r"（\1）", item_name)
            quantity = quantity if quantity.startswith("×") else f"×{quantity}"
            reward_lines.append(f"• {item_name} {quantity}｜{duration}")
        if reward_lines:
            return "🎁 道具獎勵\n\n" + "\n".join(reward_lines)

    generic_rows: list[str] = []
    for values in data_rows:
        fields = [
            (headers[index], value)
            for index, value in enumerate(values)
            if index < len(headers) and value
        ]
        if not fields:
            continue
        first_header, first_value = fields[0]
        row_lines = [f"• {first_header}：{first_value}"]
        row_lines.extend(
            f"{_TABLE_CONTINUATION_TOKEN}{header}：{value}"
            for header, value in fields[1:]
        )
        generic_rows.append("\n".join(row_lines))
    return "\n".join(generic_rows)


def _detail_from_html(
    value: str,
    *,
    base_url: str,
    omit_sanction_list: bool = False,
) -> AnnouncementDetail:
    """Walk DOM nodes in order, flushing text whenever a valid image is reached."""
    soup = BeautifulSoup(value, "html.parser")
    text_parts: list[str] = []
    blocks: list[AnnouncementContentBlock] = []
    links: list[str] = []
    seen_links: set[str] = set()
    seen_images: set[str] = set()

    def flush_text() -> None:
        text = _clean_text("".join(text_parts))
        text_parts.clear()
        if text:
            blocks.append(TextBlock(text))

    def render_table_cell(cell: Tag) -> str:
        parts: list[str] = []

        def render(node: object) -> None:
            if isinstance(node, NavigableString):
                parts.append(str(node))
                return
            if not isinstance(node, Tag) or node.name == "img":
                return
            if node.name == "a":
                label = node.get_text(" ", strip=True)
                href = _absolute_http_url(node.get("href"), base_url)
                if href and href not in seen_links:
                    seen_links.add(href)
                    links.append(href)
                    parts.append(_discord_link_text(label, href))
                else:
                    parts.append(label)
                return
            if node.name == "br":
                parts.append("\n")
                return
            for child in node.children:
                render(child)

        render(cell)
        return "".join(parts)

    def visit(node: object) -> None:
        if isinstance(node, NavigableString):
            text_parts.append(str(node))
            return
        if not isinstance(node, Tag):
            return
        if node.name == "table":
            if omit_sanction_list and _is_sanction_list_table(node):
                row_count = max(len(node.find_all("tr")) - 1, 0)
                LOGGER.info("Sanction list table omitted: rows=%d", row_count)
                return
            table_text = _format_table(node, render_table_cell)
            if table_text:
                if text_parts and not text_parts[-1].endswith("\n"):
                    text_parts.append("\n")
                text_parts.extend((table_text, "\n"))
            return
        if node.name == "img":
            image_url = _absolute_http_url(node.get("src"), base_url)
            if image_url and image_url not in seen_images and _is_content_image(node, image_url):
                flush_text()
                seen_images.add(image_url)
                blocks.append(ImageBlock(image_url))
            return
        if node.name == "a":
            label = node.get_text(" ", strip=True)
            href = _absolute_http_url(node.get("href"), base_url)
            if href and href not in seen_links:
                seen_links.add(href)
                links.append(href)
                text_parts.append(_discord_link_text(label, href))
            else:
                text_parts.append(label)
            text_parts.append("\n")
            return
        if node.name == "br":
            text_parts.append("\n")
            return
        is_block = node.name in _BLOCK_TAGS
        if is_block and text_parts and not text_parts[-1].endswith("\n"):
            text_parts.append("\n")
        for child in node.children:
            visit(child)
        if is_block and text_parts and not text_parts[-1].endswith("\n"):
            text_parts.append("\n")

    for child in soup.contents:
        visit(child)
    flush_text()
    plain_text = "\n".join(
        block.text for block in blocks if isinstance(block, TextBlock)
    ).strip()
    if len(plain_text) > MAX_PLAIN_TEXT_LENGTH:
        raise AnnouncementDetailError(
            "Official announcement content exceeds the safe parser limit "
            f"of {MAX_PLAIN_TEXT_LENGTH} characters"
        )
    images = tuple(block.url for block in blocks if isinstance(block, ImageBlock))
    return AnnouncementDetail(plain_text=plain_text, links=tuple(links), images=images, blocks=tuple(blocks))


def _html_to_text(value: str, *, base_url: str = "https://maplestoryclassic.beanfun.com/") -> str:
    return _detail_from_html(value, base_url=base_url).plain_text


def template_garbage_markers(value: str) -> tuple[str, ...]:
    """Return strong full-page template signatures found in announcement text."""
    normalized = value.casefold()
    return tuple(
        signature
        for signature in _TEMPLATE_GARBAGE_SIGNATURES
        if signature in normalized
    )


def is_template_garbage(value: str) -> bool:
    """Require multiple strong signatures so ordinary announcement wording is safe."""
    return len(template_garbage_markers(value)) >= 2


def _usable_detail(detail: AnnouncementDetail) -> AnnouncementDetail | None:
    if (
        not detail.blocks
        or (detail.plain_text in _NON_BODY_TEXT and not detail.images)
        or is_template_garbage(detail.plain_text)
    ):
        return None
    return detail


def _content_kind(value: str) -> str:
    soup = BeautifulSoup(value, "html.parser")
    if soup.find(("html", "body")) is not None or re.search(
        r"<!doctype\s+html", value, flags=re.IGNORECASE
    ):
        return "full HTML page"
    if soup.find() is not None:
        return "HTML fragment"
    return "plain text"


def _parse_content_value(
    value: str,
    *,
    base_url: str,
    announcement_title: str = "",
) -> tuple[AnnouncementDetail | None, str, str]:
    kind = _content_kind(value)
    if kind == "full HTML page":
        detail, selector = _html_detail(
            value,
            base_url=base_url,
            announcement_title=announcement_title,
        )
        return detail, kind, selector
    if is_template_garbage(value):
        return None, kind, "rejected-template"
    detail = _usable_detail(
        _detail_from_html(
            value,
            base_url=base_url,
            omit_sanction_list=_is_sanction_announcement(
                announcement_title, value
            ),
        )
    )
    selector = "fragment-root" if kind == "HTML fragment" else "plain-text-root"
    return detail, kind, selector


def _json_detail_with_source(
    payload: Any,
    *,
    base_url: str,
    announcement_title: str = "",
) -> tuple[AnnouncementDetail, str, int, str, str] | None:
    if not isinstance(payload, dict) or payload.get("code") not in (None, 1, "1"):
        return None
    candidates: list[tuple[str, dict[str, Any]]] = [("root", payload)]
    data = payload.get("data")
    if isinstance(data, dict):
        candidates.append(("data", data))
        dataset = data.get("myDataSet")
        if isinstance(dataset, dict):
            candidates.append(("data.myDataSet", dataset))
            table = dataset.get("table")
            if isinstance(table, dict):
                candidates.append(("data.myDataSet.table", table))
        for key in ("bulletin", "detail", "result"):
            nested = data.get(key)
            if isinstance(nested, dict):
                candidates.append((f"data.{key}", nested))
    for candidate_path, candidate in candidates:
        for key in ("content", "contentHtml", "html", "body", "bulletinContent"):
            value = candidate.get(key)
            if isinstance(value, str) and value.strip():
                detail, kind, selector = _parse_content_value(
                    value,
                    base_url=base_url,
                    announcement_title=announcement_title,
                )
                if detail:
                    return (
                        detail,
                        f"{candidate_path}.{key}",
                        len(value),
                        kind,
                        selector,
                    )
    return None


def _json_detail(
    payload: Any,
    *,
    base_url: str,
    announcement_title: str = "",
) -> AnnouncementDetail | None:
    result = _json_detail_with_source(
        payload,
        base_url=base_url,
        announcement_title=announcement_title,
    )
    return result[0] if result else None


def _legacy_news_parameters(url: str) -> tuple[str, str] | None:
    parsed = urlsplit(url)
    if (
        (parsed.hostname or "").casefold() != "tw.beanfun.com"
        or parsed.path.casefold() != "/news/content.aspx"
    ):
        return None
    query = parse_qs(parsed.query)
    news_id = (query.get("news_id") or [""])[0].strip()
    service_id = (query.get("service_id") or ["0"])[0].strip()
    if not news_id.isdigit() or not service_id.isdigit():
        return None
    return news_id, service_id


def _legacy_json_detail(
    payload: Any,
    *,
    base_url: str,
    announcement_title: str = "",
) -> AnnouncementDetail | None:
    if not isinstance(payload, dict) or payload.get("ResultCode") not in (1, "1"):
        return None
    result_data = payload.get("ResultData")
    if not isinstance(result_data, dict):
        return None
    contents = result_data.get("Contents")
    if not isinstance(contents, str) or not contents.strip():
        return None
    detail, _, _ = _parse_content_value(
        contents,
        base_url=base_url,
        announcement_title=announcement_title,
    )
    return detail


def _html_detail(
    html: str,
    *,
    base_url: str,
    announcement_title: str = "",
) -> tuple[AnnouncementDetail | None, str]:
    if not isinstance(html, str) or not html.strip():
        return None, "document"
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["head", "title", "script", "style", "nav", "header", "footer", "noscript", "aside", "form"]):
        tag.decompose()
    for tag in soup.select(".breadcrumb, .page-title, .bulletin-list, .announcement-list, .related-news"):
        tag.decompose()
    for selector in _HTML_CONTENT_SELECTORS:
        container = soup.select_one(selector)
        if container is None:
            continue
        for heading in container.select("h1"):
            heading.decompose()
        container_html = str(container)
        detail = _usable_detail(
            _detail_from_html(
                container_html,
                base_url=base_url,
                omit_sanction_list=_is_sanction_announcement(
                    announcement_title, container_html
                ),
            )
        )
        if detail:
            return detail, selector
    if is_template_garbage(html):
        return None, "rejected-template"
    return None, "not-found"


def fetch_announcement_detail(announcement: Announcement, *, timeout: float, user_agent: str, session: requests.Session | None = None) -> AnnouncementDetail:
    """Fetch Detail API first, then fall back to the public announcement page."""
    client = session or requests.Session()
    owns_session = session is None
    headers = {"User-Agent": user_agent, "Accept": "application/json, text/html;q=0.9"}
    detail_url = f"{DETAIL_API_URL}?pbid={announcement.announcement_id}"
    try:
        LOGGER.info("Announcement ID=%s", announcement.announcement_id)
        LOGGER.info("Detail API URL=%s", detail_url)
        try:
            response = client.post(DETAIL_API_URL, params={"pbid": announcement.announcement_id}, headers=headers, timeout=timeout)
            LOGGER.info("Detail API HTTP Status Code=%s", getattr(response, "status_code", "unknown"))
            response.raise_for_status()
            result = _json_detail_with_source(
                response.json(),
                base_url=announcement.url,
                announcement_title=announcement.title,
            )
            if result:
                detail, field_path, raw_length, content_kind, selector = result
                LOGGER.info("Detail API Content Field=%s", field_path)
                LOGGER.info("Detail API Content Type=%s", content_kind)
                LOGGER.info("Detail API Content Length=%d", raw_length)
                LOGGER.info("Detail API Parsed Text Length=%d", len(detail.plain_text))
                LOGGER.info("Detail API success")
                LOGGER.info("HTML Fallback=False")
                LOGGER.info("HTML selector=%s", selector)
                LOGGER.info("HTML extracted length=%d", len(detail.plain_text))
                LOGGER.info("Final sent content length=%d", len(detail.plain_text))
                return detail
            LOGGER.info("Detail API Content Length=0")
        except (requests.RequestException, ValueError, TypeError, AttributeError) as exc:
            LOGGER.info("Detail API unavailable: %s", type(exc).__name__)

        legacy_parameters = _legacy_news_parameters(announcement.url)
        if legacy_parameters is not None:
            news_id, service_id = legacy_parameters
            try:
                LOGGER.info(
                    "Legacy Detail API URL=%s Announcement ID=%s News ID=%s",
                    LEGACY_NEWS_API_URL,
                    announcement.announcement_id,
                    news_id,
                )
                response = client.post(
                    LEGACY_NEWS_API_URL,
                    data={"NewsID": news_id, "ServiceDataID": service_id},
                    headers=headers,
                    timeout=timeout,
                )
                LOGGER.info(
                    "Legacy Detail API HTTP Status Code=%s",
                    getattr(response, "status_code", "unknown"),
                )
                response.raise_for_status()
                detail = _legacy_json_detail(
                    response.json(),
                    base_url=announcement.url,
                    announcement_title=announcement.title,
                )
                LOGGER.info(
                    "Legacy Detail API Content Length=%d",
                    len(detail.plain_text if detail else ""),
                )
                if detail:
                    LOGGER.info("Legacy Detail API success")
                    LOGGER.info("HTML Fallback=False")
                    LOGGER.info("Final sent content length=%d", len(detail.plain_text))
                    return detail
            except (requests.RequestException, ValueError, TypeError, AttributeError) as exc:
                LOGGER.info("Legacy Detail API unavailable: %s", type(exc).__name__)

        LOGGER.info("HTML Fallback=True")
        response = client.get(announcement.url, headers=headers, timeout=timeout)
        response.raise_for_status()
        detail, selector = _html_detail(
            response.text,
            base_url=announcement.url,
            announcement_title=announcement.title,
        )
        LOGGER.info("HTML selector=%s", selector)
        LOGGER.info("HTML extracted length=%d", len(detail.plain_text if detail else ""))
    except requests.RequestException as exc:
        LOGGER.warning(
            "Announcement detail parse failure: ID=%s title=%s reason=%s",
            announcement.announcement_id,
            announcement.title,
            type(exc).__name__,
        )
        raise AnnouncementDetailError("Unable to fetch official announcement content") from exc
    finally:
        if owns_session:
            client.close()
    if not detail:
        if selector == "rejected-template":
            reason = "HTML fallback rejected suspected full-page website template"
        else:
            reason = (
                "official APIs returned no body and no approved HTML content "
                "container was found"
            )
        LOGGER.warning(
            "Announcement detail parse failure: ID=%s title=%s reason=%s",
            announcement.announcement_id,
            announcement.title,
            reason,
        )
        raise AnnouncementDetailError(reason)
    LOGGER.info("Final sent content length=%d", len(detail.plain_text))
    return detail
