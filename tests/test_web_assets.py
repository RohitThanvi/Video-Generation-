import pytest

from app import web_assets
from app.project import create_project


class FakeResp:
    def __init__(self, status=200, headers=None, body=b"x" * 10):
        self.status_code = status
        self.headers = headers or {}
        self.is_redirect = status in {301, 302, 303, 307, 308}
        self._body = body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, n):
        yield self._body

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


@pytest.fixture
def pid():
    return create_project("web assets test")["id"]


def _public(monkeypatch):
    monkeypatch.setattr(web_assets, "_check_public_url", lambda url: None)


def _responses(monkeypatch, *resps):
    it = iter(resps)
    monkeypatch.setattr(web_assets.requests.Session, "get", lambda self, *a, **k: next(it))


def test_download_saves_into_typed_folder(pid, monkeypatch):
    _public(monkeypatch)
    _responses(monkeypatch, FakeResp(headers={"Content-Type": "image/png"}))
    out = web_assets.download_asset(pid, "image", "https://example.com/a/pic.png")
    assert out["path"] == "assets/images/pic.png" and out["bytes"] == 10


def test_download_infers_extension_from_content_type(pid, monkeypatch):
    _public(monkeypatch)
    _responses(monkeypatch, FakeResp(headers={"Content-Type": "image/jpeg"}))
    assert web_assets.download_asset(pid, "image", "https://example.com/get?id=1", "photo")["path"] == "assets/images/photo.jpg"


def test_download_rejects_wrong_type_and_oversize(pid, monkeypatch):
    _public(monkeypatch)
    _responses(monkeypatch, FakeResp(headers={"Content-Type": "text/html"}))
    with pytest.raises(ValueError):
        web_assets.download_asset(pid, "image", "https://example.com/page")
    monkeypatch.setattr(web_assets, "MAX_FILE_BYTES", 5)
    _responses(monkeypatch, FakeResp(headers={"Content-Type": "image/png"}))
    with pytest.raises(ValueError):
        web_assets.download_asset(pid, "image", "https://example.com/big.png")


def test_redirects_are_followed_and_rechecked(pid, monkeypatch):
    checked = []
    monkeypatch.setattr(web_assets, "_check_public_url", checked.append)
    _responses(
        monkeypatch,
        FakeResp(302, {"Location": "/real.png"}),
        FakeResp(headers={"Content-Type": "image/png"}),
    )
    web_assets.download_asset(pid, "image", "https://example.com/start")
    assert checked == ["https://example.com/start", "https://example.com/real.png"]


@pytest.mark.parametrize("url", ["http://127.0.0.1/x.png", "http://localhost/x.png", "http://169.254.169.254/x.png", "http://10.0.0.5/x.png"])
def test_private_addresses_are_blocked(pid, url):
    with pytest.raises((PermissionError, ValueError)):
        web_assets.download_asset(pid, "image", url)


def test_only_http_schemes(pid):
    with pytest.raises(ValueError):
        web_assets.download_asset(pid, "image", "file:///etc/passwd")


def test_disabled_flag(pid, monkeypatch):
    monkeypatch.setattr(web_assets, "ALLOW_WEB_ASSETS", False)
    with pytest.raises(PermissionError):
        web_assets.download_asset(pid, "image", "https://example.com/a.png")
    with pytest.raises(PermissionError):
        web_assets.search_free_images("cat")


def test_search_free_images_maps_openverse(monkeypatch):
    class R:
        def raise_for_status(self): pass
        def json(self):
            return {"results": [{"title": "Cat", "url": "https://x/c.jpg", "license": "by", "license_version": "4.0", "creator": "Ann", "foreign_landing_url": "https://x/p"}, {"title": "no url"}]}
    monkeypatch.setattr(web_assets.requests, "get", lambda *a, **k: R())
    assert web_assets.search_free_images("cat") == [{"title": "Cat", "url": "https://x/c.jpg", "license": "by 4.0", "creator": "Ann", "source_page": "https://x/p"}]
