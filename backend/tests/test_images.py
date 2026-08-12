"""Tests for the image proxy's Referer derivation — the pure part of
app.api.images. The actual network fetch isn't unit-tested (see
test_api.py's validation-only image proxy tests); this is the logic that
decides what Referer header to send, which is where the real bug was:
some sites (dapenti.com) block a *foreign* Referer, others (sspai.com's
CDN) require *a* same-site Referer to be present at all. Deriving it from
the image URL's own host satisfies both.
"""
from app.api.images import referer_for, sniff_image_type


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
