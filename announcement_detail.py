"""Retrieve ordered text and image blocks from official announcements."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup, NavigableString, Tag

from maple_parser import Announcement

DETAIL_API_URL = "https://maplestoryclassic.beanfun.com/api/Bulletin/BulletinDetail"
MAX_PLAIN_TEXT_LENGTH = 20_000
LOGGER = logging.getLogger("maple-classic-discord-center")

_NON_BODY_TEXT = frozenset({"新楓之谷：經典版官方網站", "新楓之谷：經典版", "MapleStory Classic Official Website"})
_HTML_CONTENT_SELECTORS = (".bulletin-detail__content", ".bulletin-content", ".announcement-content", ".content-detail", ".news-content", "article", "main")
_IMAGE_NOISE_MARKERS = ("spacer", "tracking", "pixel", "transparent", "blank", "logo", "icon", "button", "btn", "top", "download")
_BLOCK_TAGS = frozenset({"p", "div", "li", "ul", "ol", "table", "tr", "section", "article", "h2", "h3", "h4", "td", "th"})
_WRAPPED_MARKDOWN_LINK_RE = re.compile(
    r"(?m)^[ \t]*[【\[][ \t]*(?:\n[ \t]*)?"
    r"(?P<link>\[[^\]\n]+\]\(https?://[^\s)]+\))[ \t]*(?:\n[ \t]*)?"
    r"[】\]][ \t]*$"
)


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
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = _WRAPPED_MARKDOWN_LINK_RE.sub(r"\g<link>", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()[:MAX_PLAIN_TEXT_LENGTH]


def _absolute_http_url(value: object, base_url: str) -> str | None:
    raw = str(value or "").strip()
    if not raw or raw.startswith("data:"):
        return None
    resolved = urljoin(base_url, raw)
    parsed = urlsplit(resolved)
    return resolved if parsed.scheme in {"http", "https"} and parsed.netloc else None


def _is_tiny_dimension(value: object) -> bool:
    match = re.search(r"\d+", str(value or ""))
    return bool(match and int(match.group()) <= 1)


def _is_content_image(image: Tag, url: str) -> bool:
    if _is_tiny_dimension(image.get("width")) or _is_tiny_dimension(image.get("height")):
        return False
    metadata = " ".join([url, str(image.get("alt") or ""), str(image.get("title") or ""), " ".join(image.get("class") or ()), str(image.get("id") or "")]).casefold()
    return not any(marker in metadata for marker in _IMAGE_NOISE_MARKERS)


def _detail_from_html(value: str, *, base_url: str) -> AnnouncementDetail:
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

    def visit(node: object) -> None:
        if isinstance(node, NavigableString):
            text_parts.append(str(node))
            return
        if not isinstance(node, Tag):
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
                text_parts.append(f"[{label or href}]({href})")
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
    plain_text = _clean_text("\n".join(block.text for block in blocks if isinstance(block, TextBlock)))
    images = tuple(block.url for block in blocks if isinstance(block, ImageBlock))
    return AnnouncementDetail(plain_text=plain_text, links=tuple(links), images=images, blocks=tuple(blocks))


def _html_to_text(value: str, *, base_url: str = "https://maplestoryclassic.beanfun.com/") -> str:
    return _detail_from_html(value, base_url=base_url).plain_text


def _usable_detail(detail: AnnouncementDetail) -> AnnouncementDetail | None:
    if not detail.blocks or (detail.plain_text in _NON_BODY_TEXT and not detail.images):
        return None
    return detail


def _json_detail(payload: Any, *, base_url: str) -> AnnouncementDetail | None:
    if not isinstance(payload, dict) or payload.get("code") not in (None, 1, "1"):
        return None
    candidates: list[dict[str, Any]] = [payload]
    data = payload.get("data")
    if isinstance(data, dict):
        candidates.append(data)
        dataset = data.get("myDataSet")
        if isinstance(dataset, dict):
            candidates.append(dataset)
            table = dataset.get("table")
            if isinstance(table, dict):
                candidates.append(table)
        for key in ("bulletin", "detail", "result"):
            nested = data.get(key)
            if isinstance(nested, dict):
                candidates.append(nested)
    for candidate in candidates:
        for key in ("content", "contentHtml", "html", "body", "bulletinContent"):
            value = candidate.get(key)
            if isinstance(value, str) and value.strip():
                detail = _usable_detail(_detail_from_html(value, base_url=base_url))
                if detail:
                    return detail
    return None


def _html_detail(html: str, *, base_url: str) -> tuple[AnnouncementDetail | None, str]:
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
        detail = _usable_detail(_detail_from_html(str(container), base_url=base_url))
        if detail:
            return detail, selector
    return _usable_detail(_detail_from_html(str(soup), base_url=base_url)), "document"


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
            detail = _json_detail(response.json(), base_url=announcement.url)
            LOGGER.info("Detail API Content Length=%d", len(detail.plain_text if detail else ""))
            if detail:
                LOGGER.info("Detail API success")
                LOGGER.info("HTML Fallback=False")
                LOGGER.info("HTML selector=.bulletin-detail__content")
                LOGGER.info("HTML extracted length=0")
                LOGGER.info("Final sent content length=%d", len(detail.plain_text))
                return detail
        except (requests.RequestException, ValueError, TypeError, AttributeError) as exc:
            LOGGER.info("Detail API unavailable: %s", type(exc).__name__)
        LOGGER.info("HTML Fallback=True")
        response = client.get(announcement.url, headers=headers, timeout=timeout)
        response.raise_for_status()
        detail, selector = _html_detail(response.text, base_url=announcement.url)
        LOGGER.info("HTML selector=%s", selector)
        LOGGER.info("HTML extracted length=%d", len(detail.plain_text if detail else ""))
    except requests.RequestException as exc:
        raise AnnouncementDetailError("Unable to fetch official announcement content") from exc
    finally:
        if owns_session:
            client.close()
    if not detail:
        raise AnnouncementDetailError("Official announcement content is empty")
    LOGGER.info("Final sent content length=%d", len(detail.plain_text))
    return detail
