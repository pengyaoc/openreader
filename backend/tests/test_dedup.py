"""Tests for content-hash dedup. Pure, no I/O."""
from app.ingest.dedup import canonicalize_url, content_hash


def test_canonicalize_strips_common_tracking_params():
    url = "https://example.com/post?utm_source=x&utm_medium=y&id=42"
    assert canonicalize_url(url) == "https://example.com/post?id=42"


def test_canonicalize_strips_fragment():
    assert canonicalize_url("https://example.com/post#section") == "https://example.com/post"


def test_canonicalize_lowercases_host_not_path():
    assert canonicalize_url("https://Example.COM/Post") == "https://example.com/Post"


def test_canonicalize_drops_trailing_slash():
    assert canonicalize_url("https://example.com/post/") == "https://example.com/post"
    assert canonicalize_url("https://example.com/") == "https://example.com/"


def test_content_hash_is_stable_for_same_input():
    h1 = content_hash("https://example.com/post", "My Title")
    h2 = content_hash("https://example.com/post", "My Title")
    assert h1 == h2


def test_content_hash_differs_for_different_url():
    h1 = content_hash("https://example.com/a", "Same Title")
    h2 = content_hash("https://example.com/b", "Same Title")
    assert h1 != h2


def test_content_hash_uses_canonicalized_url():
    h1 = content_hash("https://example.com/post?utm_source=x", "Title")
    h2 = content_hash("https://example.com/post", "Title")
    assert h1 == h2


def test_content_hash_normalizes_title_whitespace_and_case():
    h1 = content_hash("https://example.com/post", "  My   Title  ")
    h2 = content_hash("https://example.com/post", "my title")
    assert h1 == h2
