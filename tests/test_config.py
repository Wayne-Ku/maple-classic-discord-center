import pytest

from config import Config


def test_thumbnail_url_is_none_when_unset(monkeypatch):
    monkeypatch.delenv("MAPLE_THUMBNAIL_URL", raising=False)

    assert Config.from_env().maple_thumbnail_url is None


def test_thumbnail_url_accepts_https_url(monkeypatch):
    monkeypatch.setenv("MAPLE_THUMBNAIL_URL", "https://cdn.example.com/maple-logo")

    assert Config.from_env().maple_thumbnail_url == "https://cdn.example.com/maple-logo"


@pytest.mark.parametrize(
    "thumbnail_url",
    [
        "http://cdn.example.com/maple-logo.png",
        "https:///maple-logo.png",
        "javascript:alert(1)",
        "data:image/png;base64,abc",
        "file:///tmp/maple-logo.png",
    ],
)
def test_thumbnail_url_rejects_non_https_or_missing_host(monkeypatch, thumbnail_url):
    monkeypatch.setenv("MAPLE_THUMBNAIL_URL", thumbnail_url)

    with pytest.raises(ValueError, match="MAPLE_THUMBNAIL_URL"):
        Config.from_env()
