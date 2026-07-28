"""Retrieve the body of an official Maple Classic announcement safely."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

import requests
from bs4 import BeautifulSoup

from maple_parser import Announcement

DETAIL_API_URL = "https://maplestoryclassic.beanfun.com/api/Bulletin/BulletinDetail"
MAX_PLAIN_TEXT_LENGTH = 20_000
LOGGER = logging.getLogger("maple-classic-discord-center")

_NON_BODY_TEXT = frozenset(
    {
        "新楓之谷：經典版官方網站",
        "新楓之谷：經典版",
        "MapleStory Classic Official Website",
    }
)
_HTML_CONTENT_SELECTORS = (
    ".bulletin-detail__content",
    ".bulletin-content",
    ".announcement-content",
    ".content-detail",
    ".news-content",
    "article",
    "main",
)


class AnnouncementDetailError(RuntimeError):
    """Raised when an announcement body cannot be obtained."""


@dataclass(frozen=True)
class AnnouncementDetail:
    plain_text: str


def _clean_text(value: str) -> str:
    text = value.replace("\xa0", " ").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()[:MAX_PLAIN_TEXT_LENGTH]


def _html_to_text(value: str) -> str:
    """Extract plain announcement text from official HTML."""
    return BeautifulSoup(value, "html.parser").get_text("\n", strip=True)

def _usable_body(value: str | None) -> str | None:
    """Keep short announcements; only empty or known page chrome is unusable."""
    text = _clean_text(value or "")
    if not text or text in _NON_BODY_TEXT:
        return None
    return text


def _json_body(payload: Any) -> str | None:
    """Extract the official Detail API body, including the current table.content path."""
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
                body = _usable_body(_html_to_text(value))
                if body:
                    return body
    return None


def _html_body(html: str) -> tuple[str, str]:
    """Return a body and the selector that produced it, without page chrome."""
    if not isinstance(html, str) or not html.strip():
        return "", "document"

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(
        ["head", "title", "script", "style", "nav", "header", "footer", "noscript", "aside", "form"]
    ):
        tag.decompose()
    for tag in soup.select(
        ".breadcrumb, .page-title, .bulletin-list, .announcement-list, .related-news"
    ):
        tag.decompose()

    for selector in _HTML_CONTENT_SELECTORS:
        container = soup.select_one(selector)
        if container is None:
            continue
        for heading in container.select("h1"):
            heading.decompose()
        body = _usable_body(_html_to_text(str(container)))
        if body:
            return body, selector

    return _usable_body(_html_to_text(str(soup))) or "", "document"


def fetch_announcement_detail(
    announcement: Announcement,
    *,
    timeout: float,
    user_agent: str,
    session: requests.Session | None = None,
) -> AnnouncementDetail:
    """Fetch the Detail API first, then fall back to the public announcement page."""
    client = session or requests.Session()
    owns_session = session is None
    headers = {"User-Agent": user_agent, "Accept": "application/json, text/html;q=0.9"}
    detail_url = f"{DETAIL_API_URL}?pbid={announcement.announcement_id}"

    try:
        LOGGER.info("Announcement ID=%s", announcement.announcement_id)
        LOGGER.info("Detail API URL=%s", detail_url)
        try:
            response = client.post(
                DETAIL_API_URL,
                params={"pbid": announcement.announcement_id},
                headers=headers,
                timeout=timeout,
            )
            LOGGER.info("Detail API HTTP Status Code=%s", getattr(response, "status_code", "unknown"))
            response.raise_for_status()
            body = _json_body(response.json())
            LOGGER.info("Detail API Content Length=%d", len(body or ""))
            if body:
                LOGGER.info("Detail API success")
                LOGGER.info("HTML Fallback=False")
                LOGGER.info("HTML selector=.bulletin-detail__content")
                LOGGER.info("HTML extracted length=0")
                LOGGER.info("Final sent content length=%d", len(body))
                return AnnouncementDetail(body)
        except (requests.RequestException, ValueError, TypeError, AttributeError) as exc:
            LOGGER.info("Detail API unavailable: %s", type(exc).__name__)

        LOGGER.info("HTML Fallback=True")
        response = client.get(announcement.url, headers=headers, timeout=timeout)
        response.raise_for_status()
        body, selector = _html_body(response.text)
        LOGGER.info("HTML selector=%s", selector)
        LOGGER.info("HTML extracted length=%d", len(body))
    except requests.RequestException as exc:
        raise AnnouncementDetailError("Unable to fetch official announcement content") from exc
    finally:
        if owns_session:
            client.close()

    if not body:
        raise AnnouncementDetailError("Official announcement content is empty")
    LOGGER.info("Final sent content length=%d", len(body))
    return AnnouncementDetail(body)
