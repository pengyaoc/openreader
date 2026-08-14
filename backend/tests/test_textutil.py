"""Tests for HTML-to-plain-text excerpting and sanitization."""
from app.ingest.textutil import (
    plain_text_excerpt,
    proxy_image_urls,
    sanitize_html,
    tighten_newsletter_whitespace,
)


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


def test_tighten_newsletter_whitespace_handles_empty_input():
    assert tighten_newsletter_whitespace("") == ""
    assert tighten_newsletter_whitespace(None) == ""


def test_tighten_newsletter_whitespace_removes_empty_spacer_paragraphs():
    html = "<p>Real content.</p><p>&nbsp;</p><p> </p><p>More content.</p>"
    result = tighten_newsletter_whitespace(html)
    assert result.count("<p>") == 2
    assert "Real content." in result
    assert "More content." in result


def test_tighten_newsletter_whitespace_collapses_consecutive_br_tags():
    html = "<p>Line one.</p><br><br><br><br><p>Line two.</p>"
    result = tighten_newsletter_whitespace(html)
    assert result.count("<br") == 1


def test_tighten_newsletter_whitespace_collapses_nbsp_runs():
    html = "<p>Preview text" + "&nbsp;" * 20 + "hidden padding</p>"
    result = tighten_newsletter_whitespace(html)
    assert "&nbsp;&nbsp;&nbsp;" not in result


def test_tighten_newsletter_whitespace_removes_fully_empty_spacer_tables():
    # Email builders nest several table/tr/td levels deep around nothing
    # but a stray &nbsp; purely to control vertical spacing in old email
    # clients — each level becomes its own block box once the reader's
    # CSS makes tables scrollable.
    html = (
        "<p>Before.</p>"
        "<table><tbody><tr><td><table><tbody><tr><td>&nbsp;</td></tr>"
        "</tbody></table></td></tr></tbody></table>"
        "<p>After.</p>"
    )
    result = tighten_newsletter_whitespace(html)
    assert "<table" not in result
    assert "Before." in result
    assert "After." in result


def test_tighten_newsletter_whitespace_removes_empty_spacer_rows_within_a_real_table():
    # Found live, 2026-08-13, on a WSJ newsletter article: 81 of 179 <tr>
    # elements were pure `<tr><td> </td></tr>` spacer rows interleaved
    # with real content rows in the *same* table — the whole-table-empty
    # check alone never catches these, since the table has real content
    # elsewhere. Each spacer <tr> rendered as its own blank line.
    html = (
        "<table><tbody>"
        "<tr><td> </td></tr>"
        "<tr><td><h4>Headline one.</h4></td></tr>"
        "<tr><td> </td></tr>"
        "<tr><td><p>Body paragraph.</p></td></tr>"
        "<tr><td> </td></tr>"
        "</tbody></table>"
    )
    result = tighten_newsletter_whitespace(html)
    assert result.count("<tr>") == 2
    assert "Headline one." in result
    assert "Body paragraph." in result


def test_tighten_newsletter_whitespace_keeps_a_table_with_only_real_rows_intact():
    html = "<table><tbody><tr><td>A</td></tr><tr><td>B</td></tr></tbody></table>"
    result = tighten_newsletter_whitespace(html)
    assert result.count("<tr>") == 2
    assert "<table" in result
