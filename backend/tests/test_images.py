"""Tests for the image proxy's Referer derivation — the pure part of
app.api.images. The actual network fetch isn't unit-tested (see
test_api.py's validation-only image proxy tests); this is the logic that
decides what Referer header to send, which is where the real bug was:
some sites (dapenti.com) block a *foreign* Referer, others (sspai.com's
CDN) require *a* same-site Referer to be present at all. Deriving it from
the image URL's own host satisfies both.
"""
from app.api.images import SsrfBlocked, _assert_safe_url, referer_for, sniff_image_type


def test_referer_is_the_images_own_origin():
    assert referer_for("https://cdnfile.sspai.com/2026/8/12/photo.jpg") == "https://cdnfile.sspai.com/"


def test_referer_preserves_scheme():
    assert referer_for("http://example.com/a.jpg") == "http://example.com/"


def test_referer_ignores_path_and_query():
    url = "https://www.dapenti.com:99/dapenti/28e6c43105/23bfe608.jpg?x=1"
    assert referer_for(url) == "https://www.dapenti.com:99/"


def test_sniff_image_type_recognizes_png():
    png_magic = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
    assert sniff_image_type(png_magic) == "image/png"


def test_sniff_image_type_recognizes_jpeg():
    jpeg_magic = b"\xff\xd8\xff\xe0" + b"\x00" * 20
    assert sniff_image_type(jpeg_magic) == "image/jpeg"


def test_sniff_image_type_recognizes_gif():
    assert sniff_image_type(b"GIF89a" + b"\x00" * 20) == "image/gif"
    assert sniff_image_type(b"GIF87a" + b"\x00" * 20) == "image/gif"


def test_sniff_image_type_recognizes_webp():
    webp = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 20
    assert sniff_image_type(webp) == "image/webp"


def test_sniff_image_type_returns_none_for_non_image_bytes():
    assert sniff_image_type(b"<html><body>not an image</body></html>") is None


def test_sniff_image_type_returns_none_for_empty_or_short_input():
    assert sniff_image_type(b"") is None
    assert sniff_image_type(b"\x89PN") is None


def test_assert_safe_url_allows_a_public_host():
    _assert_safe_url("https://example.com/a.jpg")  # must not raise


def test_assert_safe_url_rejects_loopback():
    try:
        _assert_safe_url("http://127.0.0.1/x")
        assert False, "expected SsrfBlocked"
    except SsrfBlocked:
        pass


def test_assert_safe_url_rejects_gcp_metadata_address():
    # 169.254.169.254 is link-local, not just "GCP-specific" — the guard
    # doesn't need to special-case metadata endpoints, link-local coverage
    # already includes them.
    try:
        _assert_safe_url("http://169.254.169.254/latest/meta-data/")
        assert False, "expected SsrfBlocked"
    except SsrfBlocked:
        pass


def test_assert_safe_url_rejects_private_network_ranges():
    for url in ("http://10.0.0.5/x", "http://192.168.1.1/x", "http://172.16.0.1/x"):
        try:
            _assert_safe_url(url)
            assert False, f"expected SsrfBlocked for {url}"
        except SsrfBlocked:
            pass


# --- proxy_image end to end, against a mock upstream ----------------------

import httpx
import pytest
from starlette.testclient import TestClient

from app.api import images
from app.db import connect, init_schema
from app.imgcache import ImageCache
from app.main import create_app

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture()
def proxy(tmp_path, monkeypatch):
    """A test client whose image fetches go to `upstream` (a handler the
    test sets) instead of the network, with the SSRF DNS check stubbed out
    (it has its own tests above). Returns (client, calls)."""
    conn = connect(tmp_path / "reader.db")
    init_schema(conn)
    conn.close()
    calls: list[str] = []
    state = {"handler": lambda request: httpx.Response(200, headers={"Content-Type": "image/png"}, content=_PNG)}

    def transport_handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return state["handler"](request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport_handler))
    monkeypatch.setattr(images, "_shared_client", lambda: client)
    monkeypatch.setattr(images, "_assert_safe_url", lambda url: None)
    app = create_app(db_path=tmp_path / "reader.db", require_auth=False)
    return TestClient(app), calls, state


def test_proxy_serves_a_repeat_request_from_the_disk_cache(proxy):
    client, calls, _ = proxy
    first = client.get("/api/img", params={"url": "https://cdn.example.com/a.png"})
    second = client.get("/api/img", params={"url": "https://cdn.example.com/a.png"})
    assert first.status_code == second.status_code == 200
    assert first.content == second.content == _PNG
    assert second.headers["content-type"] == "image/png"
    assert len(calls) == 1


def test_proxy_does_not_cache_failures(proxy):
    client, calls, state = proxy
    state["handler"] = lambda request: httpx.Response(404)
    assert client.get("/api/img", params={"url": "https://cdn.example.com/x.png"}).status_code == 502
    assert client.get("/api/img", params={"url": "https://cdn.example.com/x.png"}).status_code == 502
    assert len(calls) == 2


def test_proxy_rejects_an_image_over_the_size_cap(proxy, monkeypatch):
    client, _, state = proxy
    monkeypatch.setattr(images, "_MAX_BYTES", 32)
    state["handler"] = lambda request: httpx.Response(
        200, headers={"Content-Type": "image/png"}, content=_PNG
    )
    assert client.get("/api/img", params={"url": "https://cdn.example.com/big.png"}).status_code == 502


def test_proxy_follows_a_redirect_with_the_targets_own_referer(proxy):
    client, calls, state = proxy

    def handler(request):
        if request.url.host == "old.example.com":
            return httpx.Response(302, headers={"Location": "https://new.example.com/a.png"})
        assert request.headers["Referer"] == "https://new.example.com/"
        return httpx.Response(200, headers={"Content-Type": "application/octet-stream"}, content=_PNG)

    state["handler"] = handler
    resp = client.get("/api/img", params={"url": "https://old.example.com/a.png"})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"  # sniffed
    assert calls == ["https://old.example.com/a.png", "https://new.example.com/a.png"]


def test_image_cache_prunes_least_recently_used_entries(tmp_path):
    cache = ImageCache(tmp_path, max_bytes=1000)
    for i in range(5):
        cache.put(f"https://x/{i}", "image/png", b"x" * 300)
    cache.get("https://x/0")  # most recently used now
    cache.prune()
    remaining = {i for i in range(5) if cache.get(f"https://x/{i}") is not None}
    assert 0 in remaining
    assert sum(p.stat().st_size for p in tmp_path.iterdir()) <= 800
