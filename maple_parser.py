"""Fetch and normalize Maple Classic announcements from the official JSON API."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import requests

BASE_URL = "https://maplestoryclassic.beanfun.com"
ANNOUNCEMENT_API_URL = f"{BASE_URL}/api/Bulletin/FindBulletin"
CATEGORY_NAMES = {"760": "活動", "759": "更新", "758": "重要"}
MAX_PAGES = 100
MAX_ATTEMPTS = 3
MAX_RETRY_AFTER_SECONDS = 30.0
RETRYABLE_STATUS_CODES = frozenset({403, 429, 500, 502, 503, 504})
LOGGER = logging.getLogger("maple-classic-discord-center")


class MapleParserError(RuntimeError):
    """Raised when the official announcement source cannot be read safely."""


@dataclass(frozen=True)
class Announcement:
    announcement_id: str
    category: str
    title: str
    date: str
    url: str


def _announcement_url(item: dict[str, Any]) -> str:
    external_url = item.get("urlLink")
    if external_url:
        return urljoin(BASE_URL, str(external_url))
    return f"{BASE_URL}/bulletin?Bid={item['bullentinId']}"


def parse_api_response(payload: dict[str, Any]) -> tuple[list[Announcement], int]:
    try:
        if not isinstance(payload, dict):
            raise ValueError
        if payload.get("code") != 1:
            raise MapleParserError(
                f"官網 API 回報失敗：{payload.get('message') or '未知錯誤'}"
            )
        data = payload["data"]
        if not isinstance(data, dict):
            raise ValueError
        dataset = data["myDataSet"]
        if not isinstance(dataset, dict):
            raise ValueError
        rows = dataset["table"]
        system_table = dataset["systemTable"]
        if not isinstance(rows, list) or not isinstance(system_table, dict):
            raise ValueError
        raw_total_pages = system_table["totalPage"]
        if isinstance(raw_total_pages, bool):
            raise ValueError
        total_pages = int(raw_total_pages)
        if total_pages < 1 or total_pages > MAX_PAGES:
            raise ValueError
    except (KeyError, TypeError, ValueError) as exc:
        raise MapleParserError(
            f"官網 API 回應格式與預期不符，totalPage 必須是 1 到 {MAX_PAGES} 的整數。"
        ) from exc

    announcements: list[Announcement] = []
    for row in rows:
        try:
            if not isinstance(row, dict):
                raise ValueError
            announcement_id = row["bullentinId"]
            title = row["title"]
            date = row["startDate"]
            if not all(isinstance(value, str) for value in (announcement_id, title, date)):
                raise ValueError
            announcement_id = announcement_id.strip()
            title = title.strip()
            date = date.strip()
            category_id = str(row.get("bullentinCatId") or "").strip()
            if not announcement_id or not title or not date:
                raise ValueError
            announcements.append(
                Announcement(
                    announcement_id=announcement_id,
                    category=CATEGORY_NAMES.get(category_id, "綜合"),
                    title=title,
                    date=date,
                    url=_announcement_url(row),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise MapleParserError("官網公告資料缺少必要欄位。") from exc
    return announcements, total_pages


def _retry_delay(response: object, attempt: int) -> float:
    headers = getattr(response, "headers", None)
    raw_retry_after = headers.get("Retry-After") if hasattr(headers, "get") else None
    if raw_retry_after is not None:
        try:
            return min(max(float(raw_retry_after), 0.0), MAX_RETRY_AFTER_SECONDS)
        except (TypeError, ValueError):
            pass
    return float(2 ** (attempt - 1))


def _fetch_page_response(
    client: requests.Session,
    *,
    page: int,
    headers: dict[str, str],
    timeout: float,
    sleep: Callable[[float], None],
) -> requests.Response:
    for attempt in range(1, MAX_ATTEMPTS + 1):
        response = client.post(
            ANNOUNCEMENT_API_URL,
            json={"pageSize": 50, "kind": 0, "page": page, "method": 6},
            headers=headers,
            timeout=timeout,
        )
        status_code = getattr(response, "status_code", None)
        if (
            status_code in RETRYABLE_STATUS_CODES
            and attempt < MAX_ATTEMPTS
        ):
            delay = _retry_delay(response, attempt)
            LOGGER.warning(
                "官網公告 API 暫時拒絕請求，將重試："
                "page=%d status=%s attempt=%d/%d retry_in=%.1fs",
                page,
                status_code,
                attempt,
                MAX_ATTEMPTS,
                delay,
            )
            sleep(delay)
            continue
        response.raise_for_status()
        return response
    raise AssertionError("announcement API retry loop ended unexpectedly")


def fetch_announcements(
    *,
    timeout: float = 15,
    user_agent: str,
    session: requests.Session | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> list[Announcement]:
    client = session or requests.Session()
    owns_session = session is None
    headers = {
        "User-Agent": user_agent,
        "Accept": "application/json",
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
        "Content-Type": "application/json",
        "Origin": BASE_URL,
        "Referer": f"{BASE_URL}/bulletin",
    }
    all_items: list[Announcement] = []
    page = 1
    total_pages = 1

    try:
        while page <= total_pages:
            response = _fetch_page_response(
                client,
                page=page,
                headers=headers,
                timeout=timeout,
                sleep=sleep,
            )
            try:
                payload = response.json()
            except ValueError as exc:
                raise MapleParserError("官網 API 未回傳有效 JSON。") from exc
            page_items, total_pages = parse_api_response(payload)
            all_items.extend(page_items)
            page += 1
    except requests.RequestException as exc:
        raise MapleParserError(f"無法取得官網公告：{exc}") from exc
    finally:
        if owns_session:
            client.close()

    if not all_items:
        raise MapleParserError("官網 API 未回傳任何公告，為避免誤判，狀態不會更新。")

    # Preserve API order and guard against duplicated rows across pages.
    seen: set[str] = set()
    unique_items: list[Announcement] = []
    for item in all_items:
        if item.announcement_id not in seen:
            seen.add(item.announcement_id)
            unique_items.append(item)
    return unique_items
