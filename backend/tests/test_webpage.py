"""Tests for the web-page connector: turning an arbitrary HTML listing page
(no RSS) into feed entries, either by auto-detecting the repeating group
of article links or by applying a user-supplied CSS selector.
"""
from pathlib import Path

from app.connectors.webpage import (
    MAX_ITEMS,
    extract_candidates,
    extract_with_selector,
    find_feed_links,
    page_title,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _read(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


# --- auto-detection -------------------------------------------------------

def test_top_candidate_is_the_post_list_not_nav_or_sidebar():
    candidates = extract_candidates(_read("webpage_blog_list.html"), "https://acme.example/blog")
    top = candidates[0]
    titles = [e.title for e in top.entries]
    assert titles == [
        "Scaling Postgres to a billion rows",
        "Why we moved off Kubernetes",
        "A tour of our build system",
        "Incident review: the March outage",
    ]


def test_relative_urls_are_resolved_against_the_page():
    top = extract_candidates(_read("webpage_blog_list.html"), "https://acme.example/blog")[0]
    assert top.entries[0].url == "https://acme.example/blog/scaling-postgres-to-a-billion-rows"


def test_guid_is_the_canonical_url_without_tracking_params():
    top = extract_candidates(_read("webpage_blog_list.html"), "https://acme.example/blog")[0]
    assert top.entries[1].guid == "https://acme.example/blog/why-we-moved-off-kubernetes"


def test_dates_inside_the_item_container_are_parsed():
    top = extract_candidates(_read("webpage_blog_list.html"), "https://acme.example/blog")[0]
    assert top.entries[0].published_at == "2026-09-20T10:00:00+00:00"
    assert top.entries[1].published_at == "2026-09-12T00:00:00+00:00"


def test_card_text_becomes_the_summary():
    top = extract_candidates(_read("webpage_blog_list.html"), "https://acme.example/blog")[0]
    assert "partitioned our largest table" in top.entries[0].summary


def test_candidate_selector_reproduces_its_own_entries():
    html = _read("webpage_blog_list.html")
    top = extract_candidates(html, "https://acme.example/blog")[0]
    again = extract_with_selector(html, "https://acme.example/blog", top.selector)
    assert [e.url for e in again] == [e.url for e in top.entries]


def test_duplicate_links_collapse_and_offsite_links_are_ignored():
    top = extract_candidates(_read("webpage_news_list.html"), "https://city.example/news")[0]
    urls = [e.url for e in top.entries]
    assert len(urls) == 4
    assert len(set(urls)) == 4
    assert not any("elsewhere.example.org" in u for u in urls)


def test_list_items_with_sibling_time_elements():
    top = extract_candidates(_read("webpage_news_list.html"), "https://city.example/news")[0]
    assert top.entries[0].title == "Budget hearing scheduled for October"
    assert top.entries[0].published_at == "2026-09-25T00:00:00+00:00"


def test_no_candidates_when_page_has_no_repeating_links():
    html = b"<html><body><p>Just a paragraph.</p><a href='/one'>One lonely link here</a></body></html>"
    assert extract_candidates(html, "https://x.example/") == []


def test_links_to_the_page_itself_and_javascript_links_are_ignored():
    html = b"""<html><body><ul>
      <li><a href="#top">Back to the top of the page</a></li>
      <li><a href="javascript:void(0)">Open the menu drawer now</a></li>
      <li><a href="https://x.example/list">This very listing page</a></li>
    </ul></body></html>"""
    assert extract_candidates(html, "https://x.example/list") == []


# --- real-world page: Oaktree's Howard Marks memo archive -----------------

OAKTREE_URL = "https://www.oaktreecapital.com/insights/memos"


def test_oaktree_memos_top_candidate_is_memo_links_only():
    top = extract_candidates(_read("webpage_oaktree_memos.html"), OAKTREE_URL)[0]
    urls = [e.url for e in top.entries]
    assert urls, "expected memo entries"
    assert all(u.startswith("https://www.oaktreecapital.com/insights/memo/") for u in urls)


def test_oaktree_selector_excludes_pdf_compilations():
    html = _read("webpage_oaktree_memos.html")
    top = extract_candidates(html, OAKTREE_URL)[0]
    entries = extract_with_selector(html, OAKTREE_URL, top.selector)
    assert not any(e.url.endswith(".pdf") or ".pdf?" in e.url for e in entries)


def test_oaktree_first_entry_is_newest_memo_with_date():
    top = extract_candidates(_read("webpage_oaktree_memos.html"), OAKTREE_URL)[0]
    first = top.entries[0]
    assert first.title == "Shall We Repeal the Laws of Economics – Part III"
    assert first.published_at == "2026-09-22T07:00:00+00:00"


# --- user-supplied selector -----------------------------------------------

def test_custom_selector_on_anchors():
    entries = extract_with_selector(
        _read("webpage_blog_list.html"), "https://acme.example/blog", "h2.card-title a"
    )
    assert len(entries) == 4


def test_custom_selector_on_item_containers_uses_their_first_link():
    entries = extract_with_selector(
        _read("webpage_blog_list.html"), "https://acme.example/blog", "article.card"
    )
    assert entries[0].title == "Scaling Postgres to a billion rows"
    assert entries[0].published_at == "2026-09-20T10:00:00+00:00"


def test_selector_matching_nothing_returns_empty():
    assert extract_with_selector(_read("webpage_blog_list.html"), "https://acme.example/", "div.nope a") == []


def test_invalid_selector_returns_empty_rather_than_raising():
    assert extract_with_selector(_read("webpage_blog_list.html"), "https://acme.example/", "a[[[") == []


def test_results_are_capped_newest_first():
    items = "".join(
        f'<li class="i"><a href="/p/post-number-{n}">Post number {n} title</a>'
        f'<time datetime="2026-01-{(n % 28) + 1:02d}">x</time></li>'
        for n in range(80)
    )
    html = f"<html><body><ul class='l'>{items}</ul></body></html>".encode()
    entries = extract_with_selector(html, "https://x.example/", "li.i a")
    assert len(entries) == MAX_ITEMS
    dates = [e.published_at for e in entries]
    assert dates == sorted(dates, reverse=True)


# --- page metadata ---------------------------------------------------------

def test_find_feed_links_returns_absolute_rss_and_atom_links():
    links = find_feed_links(_read("webpage_with_feed_link.html"), "https://hasafeed.example/")
    assert links == ["https://hasafeed.example/feed.xml", "https://hasafeed.example/atom.xml"]


def test_page_title():
    assert page_title(_read("webpage_blog_list.html")) == "Acme Engineering Blog"


# --- modern-framework markup ------------------------------------------------

_CSS_MODULE_PAGE = b"""<html><body><ul class="List-module-scss-module__Ab12Cd__list">
""" + b"".join(
    f'''<li><a href="/news/story-{n}" class="List-module-scss-module__Ab12Cd__item">
      <div><time>Sep {20 - n}, 2026</time><span>Science</span></div>
      <span class="List-module-scss-module__Ab12Cd__title">Story number {n} headline</span></a></li>'''.encode()
    for n in range(4)
) + b"</ul></body></html>"


def test_css_module_hashes_are_kept_out_of_the_selector():
    top = extract_candidates(_CSS_MODULE_PAGE, "https://x.example/news")[0]
    assert "Ab12Cd" not in top.selector
    rehashed = _CSS_MODULE_PAGE.replace(b"Ab12Cd", b"Zz99Yy")
    assert len(extract_with_selector(rehashed, "https://x.example/news", top.selector)) == 4


def test_card_links_use_their_inner_title_not_the_whole_card_text():
    top = extract_candidates(_CSS_MODULE_PAGE, "https://x.example/news")[0]
    assert top.entries[0].title == "Story number 0 headline"
    assert top.entries[0].published_at == "2026-09-20T00:00:00+00:00"


def test_purely_numeric_link_text_is_not_a_title():
    html = b"<html><body><ul>" + b"".join(
        f'<li><a href="/archive/{y}">{y}</a></li>'.encode() for y in range(2020, 2027)
    ) + b"</ul></body></html>"
    assert extract_candidates(html, "https://x.example/") == []
