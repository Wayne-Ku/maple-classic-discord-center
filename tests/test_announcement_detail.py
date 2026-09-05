import logging

import pytest
import requests

from announcement_detail import (
    LEGACY_NEWS_API_URL,
    AnnouncementDetailError,
    ExternalAnnouncementWithoutBodyError,
    ImageBlock,
    MAX_PLAIN_TEXT_LENGTH,
    TextBlock,
    _html_to_text,
    fetch_announcement_detail,
    is_template_garbage,
)
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


def external_landing_page_item():
    return Announcement(
        "82526",
        "活動",
        "新楓之谷：經典版 《帳號綁定 消費回饋福利連動》",
        "2026/08/27",
        "https://maplestoryclassic-event.beanfun.com/AccountBind/Index",
    )


def legacy_item():
    return Announcement(
        "82242",
        "重要",
        "【說明】gamapass 登入驗證次數重置說明",
        "2026/07/29",
        "https://tw.beanfun.com/news/content.aspx?"
        "p=1&news_id=6057&c=1&t=2918&tc=Announcement&service_id=0",
    )


ANNOUNCEMENT_82242_CONTENT = """
<p>親愛的會員 您好：</p>
<p>今日 <span>14:00～14:40</span> 因流量眾多，影響部分玩家登入 gamapass 無法驗證</p>
<p>我們預計 15:45~16:00 陸續進行驗證重置，請您稍後再次嘗試登入。</p>
<p>造成您的不便，敬請見諒，感謝您的理解與支持。</p>
<p>若您有任何疑問，歡迎參閱遊戲橘子常見問題，或聯繫客服中心協助處理。</p>
<p>電話服務專線：(02)2192-6100（請按 2）</p>
<p>遊戲橘子問題回報中心：
<a href="https://games.crm.gamania.com/hc/zh-tw/requests/new">
https://games.crm.gamania.com/hc/zh-tw/requests/new</a></p>
<p>遊戲橘子客服中心 敬上</p>
"""

FULL_PAGE_TEMPLATE = """
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN"
"http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">
<html><body onload="onLoad();">
<!-- 20121130 beanfun end-year ad cover -->
<!-- Start search bar --><!-- End search bar -->
<!-- start google ad --><!-- end google ad -->
<!-- Begin: Pagination --><!-- End: Pagination -->
<iframe></iframe><script>comScore()</script>
</body></html>
"""

ANNOUNCEMENT_82526_EXTERNAL_PAGE = """
<!doctype html>
<html>
  <head><title>新楓之谷：經典版 帳號綁定</title></head>
  <body class="body content">
    <nav><a href="/">網站導覽</a></nav>
    <main class="main pic">
      <div class="log-block pic">
        新楓之谷經典版帳號：{{ info.classicAccount }}
        <a class="login-btn" href="/login">登入帳號並進行綁定</a>
      </div>
      <div class="explain-block pic">
        <article class="explain-article">
          <p>1. 每組帳號僅能進行一次綁定，綁定後不得解除。</p>
          <p>2. 具體的回饋對照及優惠內容，請參考
            <a href="https://maplestory-event.beanfun.com/eventad/eventad?eventadid=12590">《新楓之谷》VIP系統介紹網頁</a>
          </p>
          <p>* 此項回饋並不會發放VIP點數。 *</p>
        </article>
      </div>
      <div class="notify-block pic">
        <article class="notify-article">
          <p>1. 僅能綁定相同遊戲橘子帳號旗下的遊戲帳號。</p>
          <p>2. 於2026/11/06(含)前綁定，可回饋7/29後的消費紀錄。</p>
          <p>3. 活動內容如有調整，將由官方另行公告。</p>
        </article>
      </div>
    </main>
    <dialog>選擇新楓之谷帳號</dialog>
    <script>window.app = true;</script>
  </body>
</html>
"""

ANNOUNCEMENT_82273_CONTENT = """
<p>親愛的冒險者們：</p>
<p>為了感謝各位冒險者對《新楓之谷：經典版》的支持與包容，營運團隊特別準備了「開服三日感恩回饋禮」。</p>
<div>
  <table><tbody>
    <tr><td><p><strong>道具名稱</strong></p></td><td><p><strong>數量</strong></p></td><td><p><strong>期限</strong></p></td></tr>
    <tr><td><p>經驗值1.5倍券(30分鐘)</p></td><td><p>2</p></td><td><p>14天</p></td></tr>
    <tr><td><p>選擇欄位4格擴充券</p></td><td><p>1</p></td><td><p>14天</p></td></tr>
    <tr><td><p>蛋糕</p></td><td><p>100</p></td><td><p>永久</p></td></tr>
    <tr><td><p>回家卷軸</p></td><td><p>10</p></td><td><p>永久</p></td></tr>
  </tbody></table>
</div>
<p>※獎勵領取時間：2026-07-31 ~ 2026-08-09 23:59</p>
<p>敬祝各位冒險者們遊戲愉快～</p>
<p>《新楓之谷：經典版》營運團隊 敬上</p>
"""


def announcement_82279_content(row_count=450):
    rows = "".join(
        f"<tr><td>測試角色{index:04d}</td><td>永久鎖定</td></tr>"
        for index in range(row_count)
    )
    return (
        "<p>親愛的冒險者們：</p>"
        "<p>以下為遊戲異常行為制裁名單。</p>"
        "<table><thead><tr><th>角色名稱</th><th>制裁結果</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
        "<p>營運團隊重申與叮嚀：</p>"
        "<p>請冒險者切勿使用任何非官方授權之輔助程式。</p>"
        "<p>《新楓之谷：經典版》營運團隊 敬上</p>"
    )


def announcement_82309_content(row_count=3):
    rows = "".join(
        f"<tr><td>不應顯示角色{index:04d}</td><td>永久鎖定</td></tr>"
        for index in range(row_count)
    )
    return (
        "<p>親愛的冒險者們：</p>"
        "<p>為了維護優良的遊戲環境與確保全體玩家的公平性，營運團隊持續進行查緝。</p>"
        "<p>以下帳號因嚴重違反遊戲規章，已執行「永久鎖定」處分。</p>"
        "<p>處置對象角色數量：共18,924名</p>"
        "<table><thead><tr><th>角色名稱</th><th>制裁結果</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
        "<p>營運團隊重申與叮嚀：</p>"
        "<p>請冒險者切勿抱持僥倖心態，安裝或使用任何非官方授權之輔助程式。</p>"
        "<p>打造一個公平、乾淨的經典回憶，需要所有冒險者共同守護。</p>"
        "<p>感謝大家的配合與支持！</p>"
        "<p>《新楓之谷：經典版》營運團隊 敬上</p>"
    )


def announcement_82614_content():
    return (
        "<p>親愛的冒險者們：</p>"
        "<p>以下帳號因嚴重違反遊戲規章，已執行「永久鎖定」處分。</p>"
        "<p>處置對象角色數量：共4,217名</p>"
        "<table><thead><tr><th colspan='5'>角色名稱</th></tr></thead>"
        "<tbody>"
        "<tr><td>不應顯示甲</td><td>不應顯示乙</td><td>不應顯示丙</td>"
        "<td>不應顯示丁</td><td>不應顯示戊</td></tr>"
        "<tr><td>不應顯示己</td><td>不應顯示庚</td><td>不應顯示辛</td>"
        "<td>不應顯示壬</td><td>不應顯示癸</td></tr>"
        "</tbody></table>"
        "<p>營運團隊重申與叮嚀：</p>"
        "<p>請冒險者切勿使用任何非官方授權之輔助程式。</p>"
        "<p>《新楓之谷：經典版》營運團隊 敬上</p>"
    )


def sanction_announcement(title="新楓之谷：經典版《0804(二)遊戲異常行為制裁公告》"):
    return Announcement(
        "82309",
        "重要",
        title,
        "2026/08/04",
        "https://maplestoryclassic.beanfun.com/bulletin?Bid=82309",
    )


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


def test_82526_empty_detail_api_uses_allowlisted_external_content(caplog):
    session = Session(
        [
            Response(
                {
                    "code": 1,
                    "data": {
                        "myDataSet": {
                            "table": {
                                "bullentinId": "82526",
                                "content": None,
                            }
                        }
                    },
                }
            )
        ],
        [Response(text=ANNOUNCEMENT_82526_EXTERNAL_PAGE)],
    )

    with caplog.at_level(logging.INFO):
        detail = fetch_announcement_detail(
            external_landing_page_item(),
            timeout=1,
            user_agent="test",
            session=session,
        )

    assert len(session.post_calls) == 1
    assert len(session.get_calls) == 1
    assert detail.plain_text.startswith("📌 活動說明")
    assert "每組帳號僅能進行一次綁定" in detail.plain_text
    assert (
        "[《新楓之谷》VIP系統介紹網頁]"
        "(https://maplestory-event.beanfun.com/eventad/eventad?eventadid=12590)"
        in detail.plain_text
    )
    assert "⚠️ 注意事項" in detail.plain_text
    assert "2026/11/06(含)前綁定" in detail.plain_text
    assert "{{ info.classicAccount }}" not in detail.plain_text
    assert "登入帳號並進行綁定" not in detail.plain_text
    assert "網站導覽" not in detail.plain_text
    assert "選擇新楓之谷帳號" not in detail.plain_text
    assert "Detail API Content Length=0" in caplog.text
    assert (
        "HTML selector=external:.explain-article+.notify-article"
        in caplog.text
    )


def test_82526_missing_allowlisted_external_content_uses_safe_link_only_error():
    session = Session(
        [
            Response(
                {
                    "code": 1,
                    "data": {
                        "myDataSet": {
                            "table": {
                                "bullentinId": "82526",
                                "content": None,
                            }
                        }
                    },
                }
            )
        ],
        [Response(text=FULL_PAGE_TEMPLATE)],
    )

    with pytest.raises(
        ExternalAnnouncementWithoutBodyError,
        match="Official external-link announcement has no supported inline content",
    ):
        fetch_announcement_detail(
            external_landing_page_item(),
            timeout=1,
            user_agent="test",
            session=session,
        )


def test_82279_detail_api_html_fragment_is_parsed_without_container_or_fallback(caplog):
    content = announcement_82279_content()
    session = Session(
        [
            Response(
                {
                    "code": 1,
                    "data": {
                        "myDataSet": {"table": {"content": content}}
                    },
                }
            )
        ]
    )
    announcement = Announcement(
        "82279",
        "重要",
        "新楓之谷：經典版《0802(日)遊戲異常行為制裁公告》",
        "2026/08/02",
        "https://maplestoryclassic.beanfun.com/bulletin?Bid=82279",
    )

    with caplog.at_level(logging.INFO):
        detail = fetch_announcement_detail(
            announcement,
            timeout=1,
            user_agent="test",
            session=session,
        )

    assert len(detail.plain_text) > 6000
    assert detail.plain_text.startswith("親愛的冒險者們：")
    assert "• 角色名稱：測試角色0000\n  制裁結果：永久鎖定" in detail.plain_text
    assert "• 角色名稱：測試角色0449\n  制裁結果：永久鎖定" in detail.plain_text
    assert detail.plain_text.endswith("《新楓之谷：經典版》營運團隊 敬上")
    assert not session.get_calls
    assert "Detail API Content Field=data.myDataSet.table.content" in caplog.text
    assert "Detail API Content Type=HTML fragment" in caplog.text
    assert f"Detail API Content Length={len(content)}" in caplog.text
    assert "HTML selector=fragment-root" in caplog.text
    assert "HTML extracted length=0" not in caplog.text
    assert "HTML Fallback=False" in caplog.text


def test_82309_sanction_announcement_omits_account_list_table(caplog):
    content = announcement_82309_content()
    session = Session(
        [
            Response(
                {
                    "code": 1,
                    "data": {"myDataSet": {"table": {"content": content}}},
                }
            )
        ]
    )

    with caplog.at_level(logging.INFO):
        detail = fetch_announcement_detail(
            sanction_announcement(),
            timeout=1,
            user_agent="test",
            session=session,
        )

    assert detail.plain_text.startswith("親愛的冒險者們：")
    assert "以下帳號因嚴重違反遊戲規章" in detail.plain_text
    assert "處置對象角色數量：共18,924名" in detail.plain_text
    assert "營運團隊重申與叮嚀：" in detail.plain_text
    assert detail.plain_text.endswith("《新楓之谷：經典版》營運團隊 敬上")
    assert "角色名稱" not in detail.plain_text
    assert "制裁結果" not in detail.plain_text
    assert "不應顯示角色" not in detail.plain_text
    assert "Sanction list table omitted: rows=3" in caplog.text
    assert not session.get_calls


def test_82614_sanction_announcement_omits_multi_name_account_table(caplog):
    content = announcement_82614_content()
    session = Session(
        [Response({"data": {"myDataSet": {"table": {"content": content}}}})]
    )
    announcement = Announcement(
        "82614",
        "重要",
        "新楓之谷：經典版《0903(四)遊戲異常行為制裁公告》",
        "2026/09/04",
        "https://maplestoryclassic.beanfun.com/bulletin?Bid=82614",
    )

    with caplog.at_level(logging.INFO):
        detail = fetch_announcement_detail(
            announcement,
            timeout=1,
            user_agent="test",
            session=session,
        )

    assert "以下帳號因嚴重違反遊戲規章" in detail.plain_text
    assert "處置對象角色數量：共4,217名" in detail.plain_text
    assert "營運團隊重申與叮嚀：" in detail.plain_text
    assert detail.plain_text.endswith("《新楓之谷：經典版》營運團隊 敬上")
    assert "角色名稱" not in detail.plain_text
    assert "不應顯示" not in detail.plain_text
    assert "Sanction list table omitted: rows=2" in caplog.text


@pytest.mark.parametrize(
    ("title", "content"),
    [
        ("一般重要公告", announcement_82309_content()),
        (
            "新楓之谷：經典版《0804(二)遊戲異常行為制裁公告》",
            announcement_82309_content().replace("以下帳號因", "以下帳號由於"),
        ),
    ],
)
def test_sanction_list_omission_requires_both_title_and_body_markers(
    title, content
):
    session = Session([Response({"data": {"content": content}})])

    detail = fetch_announcement_detail(
        sanction_announcement(title),
        timeout=1,
        user_agent="test",
        session=session,
    )

    assert "• 角色名稱：不應顯示角色0000\n  制裁結果：永久鎖定" in detail.plain_text


def test_sanction_detection_does_not_remove_other_table_formats():
    content = "<p>以下帳號因違規受到處分。</p>" + ANNOUNCEMENT_82273_CONTENT
    session = Session([Response({"data": {"content": content}})])

    detail = fetch_announcement_detail(
        sanction_announcement(),
        timeout=1,
        user_agent="test",
        session=session,
    )

    assert "🎁 道具獎勵" in detail.plain_text
    assert "• 經驗值1.5倍券（30分鐘） ×2｜14天" in detail.plain_text
    assert "• 回家卷軸 ×10｜永久" in detail.plain_text


def test_sanction_list_is_also_omitted_in_html_fallback():
    session = Session(
        [Response({"code": 1, "data": {"myDataSet": {"table": {"content": None}}}})],
        [Response(text=page(announcement_82309_content()))],
    )

    detail = fetch_announcement_detail(
        sanction_announcement(),
        timeout=1,
        user_agent="test",
        session=session,
    )

    assert "處置對象角色數量：共18,924名" in detail.plain_text
    assert "不應顯示角色" not in detail.plain_text
    assert len(session.get_calls) == 1


def test_detail_api_full_page_uses_approved_content_container():
    session = Session(
        [
            Response(
                {
                    "data": {
                        "content": page("<p>完整頁面中的合法公告正文</p>")
                    }
                }
            )
        ]
    )

    detail = fetch_announcement_detail(
        item(), timeout=1, user_agent="test", session=session
    )

    assert detail.plain_text == "完整頁面中的合法公告正文"
    assert not session.get_calls


def test_detail_api_full_page_without_approved_container_is_not_sent_as_body():
    session = Session(
        [Response({"data": {"content": FULL_PAGE_TEMPLATE}})],
        [Response(text=FULL_PAGE_TEMPLATE)],
    )

    with pytest.raises(AnnouncementDetailError):
        fetch_announcement_detail(
            item(), timeout=1, user_agent="test", session=session
        )

    assert len(session.get_calls) == 1


def test_short_detail_api_body_is_valid_and_does_not_fallback():
    session = Session([Response({"code": 1, "data": {"myDataSet": {"table": {"content": "<p>維護延期</p>"}}}})])
    detail = fetch_announcement_detail(item(), timeout=1, user_agent="test", session=session)

    assert detail.plain_text == "維護延期"
    assert not session.get_calls


def test_82242_uses_official_legacy_detail_api_and_preserves_content_link():
    session = Session(
        [
            Response(
                {
                    "code": 1,
                    "data": {"myDataSet": {"table": {"content": None}}},
                }
            ),
            Response(
                {
                    "ResultCode": 1,
                    "ResultData": {
                        "NewsID": 6057,
                        "Title": "gamapass 登入驗證次數重置說明",
                        "Contents": ANNOUNCEMENT_82242_CONTENT,
                    },
                }
            ),
        ]
    )

    detail = fetch_announcement_detail(
        legacy_item(),
        timeout=1,
        user_agent="test",
        session=session,
    )

    assert "親愛的會員 您好" in detail.plain_text
    assert "今日 14:00～14:40" in detail.plain_text
    assert "我們預計 15:45～16:00" in detail.plain_text
    assert "電話服務專線：(02)2192-6100（請按 2）" in detail.plain_text
    support_url = "https://games.crm.gamania.com/hc/zh-tw/requests/new"
    assert detail.plain_text.count(support_url) == 1
    assert f"[{support_url}]" not in detail.plain_text
    assert f"({support_url})" not in detail.plain_text
    assert "遊戲橘子客服中心 敬上" in detail.plain_text
    assert all(
        value.casefold() not in detail.plain_text.casefold()
        for value in (
            "DOCTYPE",
            "XHTML",
            "W3C",
            "Start search bar",
            "google ad",
            "Pagination",
            "iframe",
            "comScore",
            "beanfun end-year ad cover",
        )
    )
    assert len(session.post_calls) == 2
    assert session.post_calls[1][0][0] == LEGACY_NEWS_API_URL
    assert session.post_calls[1][1]["data"] == {
        "NewsID": "6057",
        "ServiceDataID": "0",
    }
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

    assert detail.plain_text == "更新日期：2026/07/28\n正文一\n條件 A\n• 欄位一：時間\n  欄位二：10:00"
    assert all(value not in detail.plain_text for value in ("官方網站", "頁首", "導覽", "麵包屑", "側欄", "表單", "頁尾", "bad"))


def test_82273_reward_table_keeps_rows_columns_and_following_text_in_dom_order():
    session = Session(
        [
            Response(
                {
                    "code": 1,
                    "data": {
                        "myDataSet": {
                            "table": {"content": ANNOUNCEMENT_82273_CONTENT}
                        }
                    },
                }
            )
        ]
    )

    detail = fetch_announcement_detail(
        Announcement(
            "82273",
            "活動",
            "新楓之谷：經典版《0731(五)開服三日感恩回饋公告》",
            "2026/07/31",
            "https://maplestoryclassic.beanfun.com/bulletin?Bid=82273",
        ),
        timeout=1,
        user_agent="test",
        session=session,
    )

    expected_rows = (
        "• 經驗值1.5倍券（30分鐘） ×2｜14天",
        "• 選擇欄位4格擴充券 ×1｜14天",
        "• 蛋糕 ×100｜永久",
        "• 回家卷軸 ×10｜永久",
    )
    assert detail.plain_text.count("🎁 道具獎勵") == 1
    assert "道具名稱" not in detail.plain_text
    assert "\n數量\n" not in detail.plain_text
    assert "\n期限\n" not in detail.plain_text
    assert "道具名稱\n數量\n期限" not in detail.plain_text
    assert "經驗值1.5倍券(30分鐘)\n2\n14天" not in detail.plain_text
    assert all(detail.plain_text.count(row) == 1 for row in expected_rows)
    positions = [detail.plain_text.index(row) for row in expected_rows]
    assert positions == sorted(positions)
    assert positions[-1] < detail.plain_text.index("※獎勵領取時間：2026-07-31 ～ 2026-08-09 23:59")
    assert detail.plain_text.index("※獎勵領取時間") < detail.plain_text.index("敬祝各位冒險者們遊戲愉快")
    assert detail.plain_text.index("敬祝各位冒險者們遊戲愉快") < detail.plain_text.index("《新楓之谷：經典版》營運團隊 敬上")
    assert not session.get_calls


def test_unknown_table_uses_generic_rows_and_keeps_links_clickable():
    html = """
    <p>表格前</p>
    <table>
      <tr><td>A</td><td><a href="/details">詳細資料</a></td><td>啟用</td></tr>
      <tr><td>B</td><td>一般說明</td><td>停用</td></tr>
    </table>
    <p>表格後</p>
    """
    detail = fetch_announcement_detail(
        item(),
        timeout=1,
        user_agent="test",
        session=Session([Response({"data": {"content": html}})]),
    )

    assert (
        "• 欄位一：A\n"
        "  欄位二：[詳細資料](https://example.com/details)\n"
        "  欄位三：啟用\n"
        "• 欄位一：B\n"
        "  欄位二：一般說明\n"
        "  欄位三：停用"
    ) in detail.plain_text
    assert detail.plain_text.index("表格前") < detail.plain_text.index("• 欄位一：A")
    assert detail.plain_text.index("• 欄位一：B") < detail.plain_text.index("表格後")
    assert detail.links == ("https://example.com/details",)


def test_unknown_table_uses_thead_labels_for_each_body_row():
    html = """
    <table>
      <thead><tr><th>階段</th><th>時間</th><th>說明</th></tr></thead>
      <tbody>
        <tr><td>第一階段</td><td>10:00</td><td>開始</td></tr>
        <tr><td>第二階段</td><td>12:00</td><td>結束</td></tr>
      </tbody>
    </table>
    """
    detail = fetch_announcement_detail(
        item(),
        timeout=1,
        user_agent="test",
        session=Session([Response({"data": {"content": html}})]),
    )

    assert detail.plain_text == (
        "• 階段：第一階段\n"
        "  時間：10:00\n"
        "  說明：開始\n"
        "• 階段：第二階段\n"
        "  時間：12:00\n"
        "  說明：結束"
    )
    assert detail.plain_text.count("• 階段：") == 2


def test_82337_styled_td_header_preserves_real_column_names():
    html = """
    <p>登入就送以下好禮：</p>
    <table>
      <tbody>
        <tr>
          <td style="background-color:#a4c2f4">品名</td>
          <td style="background-color:#a4c2f4">數量</td>
          <td style="background-color:#a4c2f4">期限</td>
          <td style="background-color:#a4c2f4">備註</td>
        </tr>
        <tr>
          <td style="background-color:#a4c2f4">白色兔子寵物</td>
          <td>1</td>
          <td>不適用</td>
          <td>可使用生命水復活，魔法時間90日。</td>
        </tr>
        <tr>
          <td style="background-color:#a4c2f4">選擇型欄位4格擴充券</td>
          <td>2</td>
          <td>7日</td>
          <td>無法交換</td>
        </tr>
      </tbody>
    </table>
    <p>簡訊寄送時間：2026/07/31(五)14:00後陸續發送</p>
    """
    detail = fetch_announcement_detail(
        item(),
        timeout=1,
        user_agent="test",
        session=Session([Response({"data": {"content": html}})]),
    )

    assert detail.plain_text == (
        "登入就送以下好禮：\n\n"
        "• 品名：白色兔子寵物\n"
        "  數量：1\n"
        "  期限：不適用\n"
        "  備註：可使用生命水復活，魔法時間90日。\n"
        "• 品名：選擇型欄位4格擴充券\n"
        "  數量：2\n"
        "  期限：7日\n"
        "  備註：無法交換\n\n"
        "簡訊寄送時間：2026/07/31(五)14:00後陸續發送"
    )
    assert "欄位一" not in detail.plain_text
    assert "• 品名：品名" not in detail.plain_text


def test_full_page_template_without_approved_content_container_is_rejected():
    session = Session(
        [
            Response(
                {
                    "code": 1,
                    "data": {"myDataSet": {"table": {"content": None}}},
                }
            )
        ],
        [Response(text=FULL_PAGE_TEMPLATE)],
    )

    with pytest.raises(
        AnnouncementDetailError,
        match="suspected full-page website template",
    ):
        fetch_announcement_detail(
            item(),
            timeout=1,
            user_agent="test",
            session=session,
        )

    assert len(session.get_calls) == 1


def test_template_safety_requires_multiple_strong_markers():
    assert is_template_garbage(FULL_PAGE_TEMPLATE) is True
    assert is_template_garbage("公告說明：comScore 統計方式調整。") is False


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


def test_body_over_safe_parser_limit_fails_instead_of_silent_truncation():
    session = Session([Response({"data": {"content": "<p>" + "x" * (MAX_PLAIN_TEXT_LENGTH + 50) + "</p>"}})])
    with pytest.raises(AnnouncementDetailError, match="exceeds the safe parser limit"):
        fetch_announcement_detail(item(), timeout=1, user_agent="test", session=session)


def test_82289_sized_sanction_table_above_previous_limit_is_accepted():
    row_count = 15_805
    rows = "".join(
        f"<tr><td>測試角色{index:05d}</td><td>永久鎖定</td></tr>"
        for index in range(row_count)
    )
    html = (
        "<p>親愛的冒險者們：</p>"
        "<table><tr><th>角色名稱</th><th>制裁結果</th></tr>"
        f"{rows}</table>"
        "<p>《新楓之谷：經典版》營運團隊 敬上</p>"
    )
    session = Session([Response({"data": {"content": html}})])

    detail = fetch_announcement_detail(
        item(), timeout=1, user_agent="test", session=session
    )

    assert len(detail.plain_text) > 100_000
    assert len(detail.plain_text) < MAX_PLAIN_TEXT_LENGTH
    assert detail.plain_text.count("• 角色名稱：") == row_count
    assert "• 角色名稱：測試角色00000\n  制裁結果：永久鎖定" in detail.plain_text
    assert "• 角色名稱：測試角色15804\n  制裁結果：永久鎖定" in detail.plain_text


def test_html_anchors_become_markdown_and_duplicate_urls_are_removed():
    html = (
        '<a href="/portal">官方連結</a>'
        '<a href="/portal">重複連結</a>'
        '<a href="javascript:alert(1)">不安全連結</a>'
    )
    detail = fetch_announcement_detail(
        item(),
        timeout=1,
        user_agent="test",
        session=Session([Response({"data": {"content": html}})]),
    )
    assert detail.plain_text == "[官方連結](https://example.com/portal)\n重複連結\n不安全連結"
    assert detail.links == ("https://example.com/portal",)


def test_url_label_anchor_is_kept_once_as_clickable_bare_url():
    support_url = "https://games.crm.gamania.com/hc/zh-tw/requests/new"
    html = f'<p>遊戲橘子問題回報中心：</p><p><a href="{support_url}">{support_url}</a></p>'
    detail = fetch_announcement_detail(
        item(),
        timeout=1,
        user_agent="test",
        session=Session([Response({"data": {"content": html}})]),
    )

    assert detail.plain_text == f"遊戲橘子問題回報中心：\n{support_url}"
    assert detail.plain_text.count(support_url) == 1
    assert f"[{support_url}]" not in detail.plain_text
    assert detail.links == (support_url,)


def test_wrapped_anchor_brackets_are_removed_without_touching_normal_brackets():
    html = (
        "<p>正常括號【保留】</p>"
        '<p><span>【</span><a href="/facebook">官方粉絲團</a><span>】</span></p>'
        '<p><span>[</span><a href="/instagram">Instagram 官方帳號</a><span>]</span></p>'
    )
    detail = fetch_announcement_detail(
        item(),
        timeout=1,
        user_agent="test",
        session=Session([Response({"data": {"content": html}})]),
    )

    assert "正常括號【保留】" in detail.plain_text
    assert "[官方粉絲團](https://example.com/facebook)" in detail.plain_text
    assert "[Instagram 官方帳號](https://example.com/instagram)" in detail.plain_text
    assert not any(
        line.strip() in {"【", "】", "[", "]"}
        for line in detail.plain_text.splitlines()
    )


def test_relative_content_images_are_absolute_and_noise_is_filtered():
    html = (
        '<p>正文</p><img src="/images/guide.jpg">'
        '<img src="spacer.gif" class="spacer">'
        '<img src="/pixel.gif" width="1" height="1">'
        '<img src="https://cdn.example.com/logo.png" alt="site logo">'
        '<img src="https://cdn.example.com/reward.jpg">'
    )
    detail = fetch_announcement_detail(
        item(),
        timeout=1,
        user_agent="test",
        session=Session([Response({"data": {"content": html}})]),
    )
    assert detail.images == (
        "https://example.com/images/guide.jpg",
        "https://cdn.example.com/reward.jpg",
    )
    assert "guide.jpg" not in detail.plain_text


def test_detail_blocks_follow_text_image_text_dom_order():
    html = '<p>before <a href="/event">event page</a></p><img src="/images/guide.jpg"><p>after</p>'
    detail = fetch_announcement_detail(item(), timeout=1, user_agent="test", session=Session([Response({"data": {"content": html}})]))

    assert detail.blocks == (
        TextBlock("before [event page](https://example.com/event)"),
        ImageBlock("https://example.com/images/guide.jpg"),
        TextBlock("after"),
    )


def test_detail_blocks_keep_multiple_images_and_intervening_text_in_order():
    html = '<p>???</p><img src="/one.jpg"><p>???</p><img src="/two.jpg"><p>??</p>'
    detail = fetch_announcement_detail(item(), timeout=1, user_agent="test", session=Session([Response({"data": {"content": html}})]))

    assert detail.blocks == (
        TextBlock("???"),
        ImageBlock("https://example.com/one.jpg"),
        TextBlock("???"),
        ImageBlock("https://example.com/two.jpg"),
        TextBlock("??"),
    )


def test_invalid_image_does_not_remove_following_text_block():
    html = '<p>???</p><img src="data:image/gif;base64,x"><p>???</p>'
    detail = fetch_announcement_detail(item(), timeout=1, user_agent="test", session=Session([Response({"data": {"content": html}})]))

    assert detail.blocks == (TextBlock("???\n???"),)
