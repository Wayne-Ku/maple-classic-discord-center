import logging

import pytest
import requests

from announcement_detail import AnnouncementDetailError, MAX_PLAIN_TEXT_LENGTH, _html_to_text, fetch_announcement_detail
from maple_parser import Announcement


class Response:
    def __init__(self, json_body=None, text="", error=None, status_code=200):
        self.json_body, self.text, self.error, self.status_code = json_body, text, error, status_code

    def json(self):
        if isinstance(self.json_body, Exception):
            raise self.json_body
        return self.json_body

    def raise_for_status(self):
        if self.error:
            raise self.error


class Session:
    def __init__(self, posts=(), gets=()):
        self.posts, self.gets = iter(posts), iter(gets)
        self.post_calls, self.get_calls, self.closed = [], [], False

    def post(self, *args, **kwargs):
        self.post_calls.append((args, kwargs))
        return next(self.posts)

    def get(self, *args, **kwargs):
        self.get_calls.append((args, kwargs))
        return next(self.gets)

    def close(self):
        self.closed = True


def item():
    return Announcement("82221", "活動", "公告標題", "2026/07/28", "https://example.com/bulletin")


def page(content):
    return (
        "<html><head><title>新楓之谷：經典版官方網站</title></head>"
        "<header>頁首</header><nav>導覽</nav><div class='breadcrumb'>麵包屑</div>"
        f"<div class='bulletin-detail__content'>{content}</div>"
        "<aside>側欄</aside><form>表單</form><footer>頁尾</footer>"
        "<script>bad()</script><style>bad{}</style></html>"
    )


def test_detail_api_table_content_is_preferred_without_html_fallback(caplog):
    session = Session([Response({"code": 1, "data": {"myDataSet": {"table": {"content": "<p>日期 7/23</p><p>項目 A</p>"}}}})])
    with caplog.at_level(logging.INFO):
        detail = fetch_announcement_detail(item(), timeout=1, user_agent="test", session=session)

    assert detail.plain_text == "日期 7/23\n項目 A"
    assert len(session.post_calls) == 1
    assert not session.get_calls
    assert session.closed is False
    assert "Detail API success" in caplog.text
    assert "HTML Fallback=False" in caplog.text


def test_short_detail_api_body_is_valid_and_does_not_fallback():
    session = Session([Response({"code": 1, "data": {"myDataSet": {"table": {"content": "<p>維護延期</p>"}}}})])
    detail = fetch_announcement_detail(item(), timeout=1, user_agent="test", session=session)

    assert detail.plain_text == "維護延期"
    assert not session.get_calls


def test_chrome_only_api_content_falls_back_to_html(caplog):
    session = Session(
        [Response({"code": 1, "data": {"myDataSet": {"table": {"content": "<title>新楓之谷：經典版官方網站</title>"}}}})],
        [Response(text=page("<p>活動時間：7/28</p><ul><li>活動內容</li></ul>"))],
    )
    with caplog.at_level(logging.INFO):
        detail = fetch_announcement_detail(item(), timeout=1, user_agent="test", session=session)

    assert detail.plain_text == "活動時間：7/28\n活動內容"
    assert len(session.get_calls) == 1
    assert "HTML Fallback=True" in caplog.text
    assert "HTML selector=.bulletin-detail__content" in caplog.text


def test_html_fallback_removes_chrome_and_keeps_paragraph_list_date_and_table():
    session = Session(
        [Response({}, error=requests.HTTPError())],
        [Response(text=page("<p>更新日期：2026/07/28</p><p>正文一</p><ul><li>條件 A</li></ul><table><tr><td>時間</td><td>10:00</td></tr></table>"))],
    )
    detail = fetch_announcement_detail(item(), timeout=1, user_agent="test", session=session)

    assert detail.plain_text == "更新日期：2026/07/28\n正文一\n條件 A\n時間\n10:00"
    assert all(value not in detail.plain_text for value in ("官方網站", "頁首", "導覽", "麵包屑", "側欄", "表單", "頁尾", "bad"))


@pytest.mark.parametrize(
    "post, get",
    [
        (Response({}, error=requests.HTTPError()), Response(text=page(""))),
        (Response(ValueError("bad json")), Response(error=requests.HTTPError())),
        (Response({}, error=requests.Timeout()), Response(error=requests.Timeout())),
    ],
)
def test_empty_http_error_and_timeout_raise(post, get):
    with pytest.raises(AnnouncementDetailError):
        fetch_announcement_detail(item(), timeout=1, user_agent="test", session=Session([post], [get]))


def test_external_session_is_not_closed_and_internal_session_is_closed(monkeypatch):
    external = Session([Response({"data": {"content": "<p>外部 session</p>"}})])
    fetch_announcement_detail(item(), timeout=1, user_agent="test", session=external)
    assert external.closed is False

    internal = Session([Response({"data": {"content": "<p>內部 session</p>"}})])
    monkeypatch.setattr("announcement_detail.requests.Session", lambda: internal)
    fetch_announcement_detail(item(), timeout=1, user_agent="test")
    assert internal.closed is True


def test_body_length_is_safely_limited():
    session = Session([Response({"data": {"content": "<p>" + "x" * (MAX_PLAIN_TEXT_LENGTH + 50) + "</p>"}})])
    detail = fetch_announcement_detail(item(), timeout=1, user_agent="test", session=session)
    assert len(detail.plain_text) == MAX_PLAIN_TEXT_LENGTH


def test_html_anchor_text_is_plain_text():
    html = '<a href="https://example.com/portal">官方連結</a><a href="javascript:alert(1)">不安全連結</a>'
    assert _html_to_text(html) == "官方連結\n不安全連結"
