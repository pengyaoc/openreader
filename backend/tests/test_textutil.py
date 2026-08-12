"""Tests for HTML-to-plain-text excerpting and sanitization."""
from app.ingest.textutil import plain_text_excerpt, proxy_image_urls, sanitize_html


def test_plain_text_excerpt_strips_tags():
    html = "<p>First paragraph with a <a href='x'>link</a> in it.</p><p>More text here.</p>"
    result = plain_text_excerpt(html, limit=300)
    assert "<" not in result
    assert "First paragraph" in result
    assert "More text here." in result


def test_plain_text_excerpt_truncates_to_limit():
    html = "<p>" + ("word " * 200) + "</p>"
    result = plain_text_excerpt(html, limit=50)
    assert len(result) <= 50


def test_plain_text_excerpt_handles_empty_input():
    assert plain_text_excerpt("", limit=300) == ""
    assert plain_text_excerpt(None, limit=300) == ""


def test_plain_text_excerpt_drops_hnrss_style_link_boilerplate():
    # hnrss.org (and similar auto-generated feeds) emit a description that's
    # pure link/metadata echo, not a real summary — that's not useful as a
    # subtitle telling the reader whether the article is worth opening.
    html = (
        "<p>Article URL: <a href='https://x.com/a'>https://x.com/a</a></p>"
        "<p>Comments URL: <a href='https://news.ycombinator.com/item?id=1'>"
        "https://news.ycombinator.com/item?id=1</a></p>"
        "<p>Points: 176</p><p># Comments: 19</p>"
    )
    result = plain_text_excerpt(html, limit=300)
    assert result == ""


def test_plain_text_excerpt_keeps_real_prose_and_drops_boilerplate_lines():
    html = (
        "<p>Article URL: <a href='https://x.com/a'>https://x.com/a</a></p>"
        "<p>Hey HN, we previously released Cactus Needle and have now added "
        "structured extraction for phones and robots.</p>"
        "<p>Comments URL: <a href='https://news.ycombinator.com/item?id=1'>"
        "https://news.ycombinator.com/item?id=1</a></p>"
        "<p>Points: 42</p>"
    )
    result = plain_text_excerpt(html, limit=300)
    assert "Article URL" not in result
    assert "Comments URL" not in result
    assert "Points:" not in result
    assert "structured extraction for phones and robots" in result


def test_sanitize_html_strips_script_tags():
    html = "<p>Safe text</p><script>alert('xss')</script>"
    result = sanitize_html(html)
    assert "<script>" not in result
    assert "Safe text" in result


def test_sanitize_html_strips_event_handlers():
    html = "<img src='x.png' onerror='alert(1)'>"
    result = sanitize_html(html)
    assert "onerror" not in result


def test_sanitize_html_keeps_safe_formatting_tags():
    html = "<p>Hello <b>world</b> and <a href='https://x.com'>a link</a></p>"
    result = sanitize_html(html)
    assert "<b>world</b>" in result
    assert 'href="https://x.com"' in result


def test_sanitize_html_forces_links_to_open_in_a_new_tab():
    # Every link inside article/newsletter/generated content is untrusted,
    # third-party content — it must never navigate the reader away from
    # the app in the same tab.
    html = "<p><a href='https://x.com'>a link</a></p>"
    result = sanitize_html(html)
    assert 'target="_blank"' in result
    assert "noopener" in result


def test_sanitize_html_forces_new_tab_even_if_source_html_set_a_different_target():
    html = "<a href='https://x.com' target='_self'>link</a>"
    result = sanitize_html(html)
    assert 'target="_blank"' in result
    assert "_self" not in result


def test_proxy_image_urls_rewrites_src_through_the_image_proxy():
    html = '<p>text</p><img src="https://cdn.example.com/a.jpg" alt="x">'
    result = proxy_image_urls(html)
    assert 'src="/api/img?url=https%3A%2F%2Fcdn.example.com%2Fa.jpg"' in result
    assert 'alt="x"' in result


def test_proxy_image_urls_leaves_non_image_content_untouched():
    html = "<p>Hello <b>world</b></p>"
    assert proxy_image_urls(html) == sanitize_html(html)


def test_proxy_image_urls_handles_multiple_images():
    html = '<img src="https://a.com/1.jpg"><img src="https://b.com/2.jpg">'
    result = proxy_image_urls(html)
    assert result.count("/api/img?url=") == 2


def test_proxy_image_urls_ignores_images_with_no_src():
    html = "<img>"
    result = proxy_image_urls(html)
    assert "/api/img" not in result
