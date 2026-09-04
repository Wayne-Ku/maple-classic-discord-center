import pytest
import requests

from maple_parser import (
    ANNOUNCEMENT_API_URL,
    BASE_URL,
    MAX_ATTEMPTS,
    MAX_PAGES,
    MapleParserError,
    fetch_announcements,
    parse_api_response,
)


def payload(rows, total_page=1, code=1):
    return {
        "code": code,
        "data": {
            "myDataSet": {
                "systemTable": {"totalPage": str(total_page)},
                "table": rows,
            }
        },
    }


def row(announcement_id="1", category="760", url_link=None):
    return {
        "bullentinId": announcement_id,
        "bullentinCatId": category,
        "title": f"公告 {announcement_id}",
        "startDate": "2026/07/23",
        "urlLink": url_link,
    }


class FakeResponse:
    def __init__(self, body=None, error=None, status_code=200, headers=None):
        self.body = body
        self.error = error
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self):
        if self.error:
            raise self.error

    def json(self):
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


class FakeSession:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []
        self.closed = False

    def post(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return next(self.responses)

    def close(self):
        self.closed = True


def test_parse_normal_single_page_and_urls():
    items, pages = parse_api_response(
        payload([row("1"), row("2", category="68", url_link="https://example.com/news")])
    )
    assert pages == 1
    assert items[0].category == "活動"
    assert items[0].url.endswith("/bulletin?Bid=1")
    assert items[1].category == "綜合"
    assert items[1].url == "https://example.com/news"


def test_fetches_multiple_pages_and_deduplicates_in_api_order():
    session = FakeSession(
        [
            FakeResponse(payload([row("3"), row("2")], total_page=2)),
            FakeResponse(payload([row("2"), row("1")], total_page=2)),
        ]
    )
    items = fetch_announcements(user_agent="test", session=session)
    assert [item.announcement_id for item in items] == ["3", "2", "1"]
    assert len(session.calls) == 2
    assert session.closed is False


def test_transient_403_is_retried_with_official_request_headers(caplog):
    session = FakeSession(
        [
            FakeResponse(
                error=requests.HTTPError("403 Forbidden"),
                status_code=403,
            ),
            FakeResponse(payload([row("1")])),
        ]
    )
    sleeps = []

    items = fetch_announcements(
        user_agent="test-agent",
        session=session,
        sleep=sleeps.append,
    )

    assert [item.announcement_id for item in items] == ["1"]
    assert sleeps == [1.0]
    assert len(session.calls) == 2
    args, kwargs = session.calls[0]
    assert args == (ANNOUNCEMENT_API_URL,)
    assert kwargs["headers"]["Origin"] == BASE_URL
    assert kwargs["headers"]["Referer"] == f"{BASE_URL}/bulletin"
    assert kwargs["headers"]["Accept-Language"].startswith("zh-TW")
    assert "status=403" in caplog.text
    assert "attempt=1/3" in caplog.text


def test_429_retry_after_is_respected_and_bounded():
    session = FakeSession(
        [
            FakeResponse(
                error=requests.HTTPError("429 Too Many Requests"),
                status_code=429,
                headers={"Retry-After": "90"},
            ),
            FakeResponse(payload([row("1")])),
        ]
    )
    sleeps = []

    assert fetch_announcements(
        user_agent="test",
        session=session,
        sleep=sleeps.append,
    )
    assert sleeps == [30.0]


def test_retryable_failure_stops_after_max_attempts():
    session = FakeSession(
        [
            FakeResponse(
                error=requests.HTTPError("403 Forbidden"),
                status_code=403,
            )
            for _ in range(MAX_ATTEMPTS)
        ]
    )
    sleeps = []

    with pytest.raises(MapleParserError, match="403 Forbidden"):
        fetch_announcements(
            user_agent="test",
            session=session,
            sleep=sleeps.append,
        )

    assert len(session.calls) == MAX_ATTEMPTS
    assert sleeps == [1.0, 2.0]


def test_non_retryable_400_fails_without_retry():
    session = FakeSession(
        [
            FakeResponse(
                error=requests.HTTPError("400 Bad Request"),
                status_code=400,
            )
        ]
    )

    with pytest.raises(MapleParserError, match="400 Bad Request"):
        fetch_announcements(
            user_agent="test",
            session=session,
            sleep=lambda _delay: pytest.fail("must not retry"),
        )

    assert len(session.calls) == 1


@pytest.mark.parametrize(
    "bad_payload",
    [
        None,
        [],
        {"code": 1, "data": []},
        {"code": 1, "data": {"myDataSet": []}},
        {"code": 1, "data": {"myDataSet": {"table": [], "systemTable": []}}},
        {"code": 1, "data": {"myDataSet": {"table": {}, "systemTable": {"totalPage": "1"}}}},
    ],
)
def test_parse_rejects_invalid_container_shapes(bad_payload):
    with pytest.raises(MapleParserError):
        parse_api_response(bad_payload)


def test_parse_rejects_non_success_code():
    with pytest.raises(MapleParserError):
        parse_api_response(payload([], code=0))


@pytest.mark.parametrize("bad_row", [None, "row", {}, {"bullentinId": "1", "title": "x", "startDate": ""}, {"bullentinId": 1, "title": "x", "startDate": "2026/01/01"}])
def test_parse_rejects_invalid_rows_and_required_fields(bad_row):
    with pytest.raises(MapleParserError):
        parse_api_response(payload([bad_row]))


@pytest.mark.parametrize("total_page", ["zero", "0", "-1", MAX_PAGES + 1])
def test_parse_rejects_invalid_or_excessive_total_pages(total_page):
    with pytest.raises(MapleParserError):
        parse_api_response(payload([], total_page=total_page))


def test_fetch_rejects_empty_all_pages():
    with pytest.raises(MapleParserError):
        fetch_announcements(user_agent="test", session=FakeSession([FakeResponse(payload([]))]))


def test_fetch_wraps_invalid_json_and_http_errors():
    with pytest.raises(MapleParserError):
        fetch_announcements(user_agent="test", session=FakeSession([FakeResponse(ValueError("bad json"))]))
    with pytest.raises(MapleParserError):
        fetch_announcements(
            user_agent="test",
            session=FakeSession([FakeResponse(error=requests.HTTPError("bad status"))]),
        )


def test_owned_session_is_closed(monkeypatch):
    session = FakeSession([FakeResponse(payload([row()]))])
    monkeypatch.setattr("maple_parser.requests.Session", lambda: session)
    fetch_announcements(user_agent="test")
    assert session.closed is True
