"""Tests for readability extraction: picking the main content block out of
a full page and discarding nav/ad/footer chrome.
"""
from pathlib import Path

from app.ingest.extract import extract_readable

FIXTURES = Path(__file__).parent / "fixtures"


def test_extract_picks_the_article_body_not_nav():
    html = (FIXTURES / "article_page.html").read_text()
    result = extract_readable(html)
    assert "first real paragraph" in result
    assert "second paragraph" in result
    assert "third paragraph" in result


def test_extract_excludes_navigation_chrome():
    html = (FIXTURES / "article_page.html").read_text()
    result = extract_readable(html)
    assert "Home" not in result
    assert "About" not in result


def test_extract_excludes_footer_and_scripts():
    html = (FIXTURES / "article_page.html").read_text()
    result = extract_readable(html)
    assert "Copyright 2026" not in result
    assert "tracking pixel junk" not in result


def test_extract_keeps_images_in_the_main_content():
    html = (FIXTURES / "article_page.html").read_text()
    result = extract_readable(html)
    assert "photo.jpg" in result


def test_extract_returns_empty_string_for_no_body():
    assert extract_readable("<html></html>") == ""


def test_extract_resolves_relative_image_urls_against_base_url():
    html = """
    <html><body><article>
      <p>This paragraph is long enough to be picked up by the density scorer.</p>
      <img src="/images/photo.png" alt="A photo"/>
      <p>Another sufficiently long paragraph to keep this the winning block.</p>
    </article></body></html>
    """
    result = extract_readable(html, base_url="https://blog.example.com/posts/1")
    assert 'src="https://blog.example.com/images/photo.png"' in result


def test_extract_resolves_relative_link_urls_against_base_url():
    html = """
    <html><body><article>
      <p>This paragraph is long enough to be picked up by the density scorer,
         and contains a <a href="/other-post">relative link</a> to follow.</p>
      <p>Another sufficiently long paragraph to keep this the winning block.</p>
    </article></body></html>
    """
    result = extract_readable(html, base_url="https://blog.example.com/posts/1")
    assert 'href="https://blog.example.com/other-post"' in result


def test_extract_leaves_already_absolute_urls_untouched():
    html = """
    <html><body><article>
      <p>This paragraph is long enough to be picked up by the density scorer.</p>
      <img src="https://cdn.example.com/photo.png" alt="A photo"/>
      <p>Another sufficiently long paragraph to keep this the winning block.</p>
    </article></body></html>
    """
    result = extract_readable(html, base_url="https://blog.example.com/posts/1")
    assert 'src="https://cdn.example.com/photo.png"' in result
