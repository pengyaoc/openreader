"""Web-page connector: turns an ordinary HTML listing page — a blog index,
a news list, a memo archive — into feed entries, for sites that publish no
RSS/Atom. Pure: takes bytes, returns NormalizedEntry lists; no network.

Two modes:

- extract_candidates() auto-detects the page's repeating article links. It
  groups every same-site <a href> by a *structural signature* (its own tag
  and class plus a few ancestors', e.g. `ul.news-list > li.news-item > a`)
  and ranks the groups by size × title length. Nav, header, footer and
  sidebar chrome is stripped first, so the winning group is almost always
  the content list. Each candidate's signature is itself a CSS selector, so
  the UI can show it, the user can tweak it, and it's what gets saved into
  feeds.yaml — later refreshes re-run that exact selector rather than
  re-guessing, which keeps a source's items stable as the page evolves.

- extract_with_selector() applies a (saved or user-typed) CSS selector.

Deliberately static-HTML-only (selectolax, no headless browser): pages that
render their list client-side from JSON won't yield entries. The builder
UI tells the user to try a listing sub-page instead.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from urllib.parse import urljoin, urlsplit

import msgspec
from selectolax.parser import HTMLParser, Node

from app.connectors.base import NormalizedEntry
from app.connectors.dates import parse_date
from app.ingest.dedup import canonicalize_url
from app.ingest.textutil import plain_text_excerpt

# Each scrape returns at most this many entries, newest first. Bounds the
# first refresh of a long archive page (e.g. decades of memos) to a normal
# feed's worth of items; and since the same cap applies on every refresh,
# older archive items never trickle in later — only genuinely new ones do.
MAX_ITEMS = 30

_NOISE_TAGS = ("script", "style", "noscript", "template", "nav", "header", "footer", "aside", "form")
_SELECTOR_MODE_NOISE_TAGS = ("script", "style", "noscript", "template")
_SIGNATURE_DEPTH = 3  # ancestors above the anchor included in a signature
_MIN_GROUP_SIZE = 3
_MIN_TITLE_LEN = 4
_TITLE_LEN_CAP = 60  # beyond this, longer link text stops adding to a group's score
_SUMMARY_MIN_LEN = 20
_GENERIC_LINK_TEXT = {
    "read more", "more", "continue reading", "read", "listen", "watch", "learn more",
    "next", "previous", "older posts", "newer posts", "see all", "view all", "home",
}
_CLASS_RE = re.compile(r"^[A-Za-z_][\w-]*$")
_GENERATED_CLASS_RE = re.compile(r"\d{3,}|^css-|^sc-|^jsx-")
_SASS_MODULE_RE = re.compile(r"^(?P<comp>[A-Za-z][A-Za-z0-9]*)-module-\w+-module__[\w-]+?__(?P<local>[A-Za-z][\w-]*)$")
_NEXT_MODULE_RE = re.compile(r"^(?P<comp>[A-Za-z][A-Za-z0-9]*)_(?P<local>[A-Za-z][A-Za-z0-9]*)__[\w-]{5}$")
_HEADING_SELECTOR = "h1, h2, h3, h4, h5, h6"
_TITLE_CLASS_SELECTOR = '[class*="title"], [class*="Title"], [class*="headline"], [class*="Headline"]'
_FEED_TYPES = {"application/rss+xml", "application/atom+xml", "application/feed+json"}


class Candidate(msgspec.Struct, kw_only=True):
    selector: str
    score: float
    entries: list[NormalizedEntry]


def _parse(html: bytes | str) -> HTMLParser:
    if isinstance(html, bytes):
        html = html.decode("utf-8", errors="replace")
    return HTMLParser(html)


def _strip(tree: HTMLParser, tags: tuple[str, ...]) -> None:
    for tag in tags:
        for node in tree.css(tag):
            node.decompose()


def _text(node: Node) -> str:
    return re.sub(r"\s+", " ", node.text(deep=True, separator=" ")).strip()


def _site(host: str) -> str:
    return host.lower().removeprefix("www.")


def _same_site(a: str, b: str) -> bool:
    a, b = _site(a), _site(b)
    return a == b or a.endswith("." + b) or b.endswith("." + a)


def _resolve_link(anchor: Node, page_url: str) -> str | None:
    """Absolute URL for an anchor if it's a plausible same-site article link,
    else None (javascript:, mailto:, off-site, or the page itself)."""
    href = (anchor.attributes.get("href") or "").strip()
    if not href or href.startswith("#"):
        return None
    url = urljoin(page_url, href)
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    page = urlsplit(page_url)
    if not _same_site(parts.hostname, page.hostname or ""):
        return None
    if canonicalize_url(url) == canonicalize_url(page_url):
        return None
    return url


def _usable_class(node: Node) -> str | None:
    for cls in (node.attributes.get("class") or "").split():
        if _CLASS_RE.match(cls) and not _GENERATED_CLASS_RE.search(cls) and "__" not in cls:
            return cls
    return None


def _css_module_filter(node: Node) -> str | None:
    """CSS-modules class names embed a build hash that changes on the site's
    next deploy (seen: `PublicationList-module-scss-module__KxYrHG__list`,
    and Next.js's `Card_title__x7Yz1`). Matching on the stable parts only
    — component and local name — keeps a saved selector working."""
    for cls in (node.attributes.get("class") or "").split():
        match = _SASS_MODULE_RE.match(cls)
        if match:
            return f'[class*="{match["comp"]}-module"][class*="__{match["local"]}"]'
        match = _NEXT_MODULE_RE.match(cls)
        if match:
            return f'[class*="{match["comp"]}_{match["local"]}__"]'
    return None


def _step(node: Node) -> str:
    cls = _usable_class(node)
    if cls:
        return f"{node.tag}.{cls}"
    return node.tag + (_css_module_filter(node) or "")


def _signature(anchor: Node) -> str:
    steps = [_step(anchor)]
    node = anchor.parent
    while node is not None and len(steps) <= _SIGNATURE_DEPTH and node.tag not in ("body", "html"):
        steps.append(_step(node))
        node = node.parent
    return " > ".join(reversed(steps))


def _anchor_title(anchor: Node) -> str:
    """Card-style links often wrap date, category and title together; prefer
    an inner heading or title-classed element, else the link text with any
    <time> text removed."""
    for selector in (_HEADING_SELECTOR, _TITLE_CLASS_SELECTOR):
        inner = anchor.css_first(selector)
        if inner is not None and _is_title_like(_text(inner)):
            return _text(inner)
    text = _text(anchor)
    for time_node in anchor.css("time"):
        text = text.replace(_text(time_node), "", 1)
    return re.sub(r"\s+", " ", text).strip()


def _is_title_like(text: str) -> bool:
    return (
        len(text) >= _MIN_TITLE_LEN
        and text.lower() not in _GENERIC_LINK_TEXT
        and re.search(r"[^\W\d_]", text) is not None
    )


def _path_prefix_filter(urls: list[str]) -> str:
    """When a group mixes article links with a minority of other same-site
    links (e.g. a memo list that also holds PDF compilations under /docs/),
    returns an attribute filter like `[href*="/insights/memo/"]` narrowing
    the selector to the majority's path; '' when the group is already
    homogeneous. Numeric segments (years, months, ids) are never used —
    a `/news/2026/09/` filter would silently drop next month's items."""
    segments = [[s for s in urlsplit(u).path.split("/") if s][:-1] for u in urls]
    firsts = Counter(seg[0] for seg in segments if seg)
    if not firsts:
        return ""
    first, first_count = firsts.most_common(1)[0]
    if first_count == len(urls) or first_count / len(urls) < 0.6 or first.isdigit():
        return ""
    prefix = [first]
    seconds = Counter(seg[1] for seg in segments if seg and seg[0] == first and len(seg) > 1)
    if seconds:
        second, second_count = seconds.most_common(1)[0]
        if second_count == first_count and not second.isdigit():
            prefix.append(second)
    return f'[href*="/{"/".join(prefix)}/"]'


def _loose_date(raw: str | None) -> str | None:
    """parse_date plus tolerance for what real pages put in <time>: stray
    leading characters (seen: datetime=">2026-09-22T07:00:00Z") and
    human-formatted text like "Sep 25, 2026"."""
    if not raw:
        return None
    raw = raw.strip()
    match = re.search(r"\d", raw)
    if match and re.match(r"\d{4}-\d{2}-\d{2}", raw[match.start():]):
        return parse_date(raw[match.start():])
    parsed = parse_date(raw)
    if parsed:
        return parsed
    cleaned = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", raw).replace(",", "")
    for fmt in ("%b %d %Y", "%B %d %Y", "%d %b %Y", "%d %B %Y", "%Y/%m/%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(cleaned, fmt).replace(tzinfo=UTC).isoformat()
        except ValueError:
            continue
    return None


def _entry_date(container: Node) -> str | None:
    for time_node in container.css("time"):
        parsed = _loose_date(time_node.attributes.get("datetime")) or _loose_date(_text(time_node))
        if parsed:
            return parsed
    return None


def _containers(anchors: list[Node]) -> list[Node]:
    """For each anchor, the highest ancestor that holds no other anchor of
    the group — i.e. its card / list item. That's where the item's date,
    heading and blurb live, whether they're inside the link or siblings."""
    counts: Counter[int] = Counter()
    for anchor in anchors:
        node = anchor.parent
        while node is not None:
            counts[node.mem_id] += 1
            node = node.parent
    result = []
    for anchor in anchors:
        container = anchor
        node = anchor.parent
        while node is not None and node.tag not in ("body", "html") and counts[node.mem_id] == 1:
            container = node
            node = node.parent
        result.append(container)
    return result


def _build_entries(pairs: list[tuple[Node, Node]], page_url: str) -> list[NormalizedEntry]:
    """pairs are (anchor, item-container). Dedups by canonical URL, then
    orders newest-first when the page provides dates, and caps."""
    entries: list[NormalizedEntry] = []
    seen: set[str] = set()
    for anchor, container in pairs:
        url = _resolve_link(anchor, page_url)
        if url is None:
            continue
        guid = canonicalize_url(url)
        if guid in seen:
            continue
        title = _anchor_title(anchor)
        if not _is_title_like(title):
            heading = container.css_first(_HEADING_SELECTOR)
            title = _text(heading) if heading is not None else title
        if not _is_title_like(title):
            continue
        seen.add(guid)
        rest = _text(container).replace(title, "", 1).strip()
        summary = plain_text_excerpt(rest) if len(rest) >= _SUMMARY_MIN_LEN else ""
        entries.append(
            NormalizedEntry(
                guid=guid,
                url=url,
                title=title,
                published_at=_entry_date(container),
                summary=summary,
            )
        )

    if entries and sum(1 for e in entries if e.published_at) * 2 >= len(entries):
        # Stable sort: undated items keep page order, after the dated ones.
        entries.sort(key=lambda e: e.published_at or "", reverse=True)
    return entries[:MAX_ITEMS]


def _selector_pairs(tree: HTMLParser, selector: str) -> list[tuple[Node, Node]]:
    try:
        matched = tree.css(selector)
    except Exception:  # noqa: BLE001 — selectolax raises on malformed selectors
        return []
    anchors: list[Node] = []
    explicit: dict[int, Node] = {}
    for node in matched:
        if node.tag == "a":
            anchors.append(node)
        else:
            inner = node.css_first("a[href]")
            if inner is not None:
                anchors.append(inner)
                explicit[inner.mem_id] = node
    containers = _containers(anchors)
    return [
        (a, explicit.get(a.mem_id, c)) for a, c in zip(anchors, containers)
    ]


def extract_with_selector(html: bytes | str, page_url: str, selector: str) -> list[NormalizedEntry]:
    """Entries for every element `selector` matches: anchors directly, or
    item containers (their first link is the entry's link). An invalid or
    non-matching selector yields []."""
    tree = _parse(html)
    _strip(tree, _SELECTOR_MODE_NOISE_TAGS)
    return _build_entries(_selector_pairs(tree, selector), page_url)


def extract_candidates(html: bytes | str, page_url: str, limit: int = 3) -> list[Candidate]:
    """Best-guess repeating link groups on the page, best first. Each
    candidate's entries are produced by re-running its own selector, so
    what the preview shows is exactly what refresh will ingest."""
    tree = _parse(html)
    _strip(tree, _NOISE_TAGS)
    if tree.body is None:
        return []

    groups: dict[str, list[tuple[Node, str, str]]] = defaultdict(list)
    for anchor in tree.body.css("a[href]"):
        url = _resolve_link(anchor, page_url)
        if url is None:
            continue
        title = _anchor_title(anchor)
        if not _is_title_like(title):
            continue
        groups[_signature(anchor)].append((anchor, url, title))

    scored: list[tuple[float, str]] = []
    for signature, members in groups.items():
        unique_urls = list(dict.fromkeys(canonicalize_url(u) for _, u, _ in members))
        if len(unique_urls) < _MIN_GROUP_SIZE:
            continue
        mean_len = sum(min(len(t), _TITLE_LEN_CAP) for _, _, t in members) / len(members)
        selector = signature + _path_prefix_filter([u for _, u, _ in members])
        scored.append((len(unique_urls) * mean_len, selector))
    scored.sort(key=lambda s: s[0], reverse=True)

    candidates: list[Candidate] = []
    full_tree = _parse(html)
    _strip(full_tree, _SELECTOR_MODE_NOISE_TAGS)
    for score, selector in scored:
        entries = _build_entries(_selector_pairs(full_tree, selector), page_url)
        if len(entries) < _MIN_GROUP_SIZE:
            continue
        candidates.append(Candidate(selector=selector, score=score, entries=entries))
        if len(candidates) >= limit:
            break
    return candidates


def find_feed_links(html: bytes | str, page_url: str) -> list[str]:
    """RSS/Atom/JSON feeds the page advertises via <link rel="alternate">
    — if there is one, subscribing to it beats scraping."""
    tree = _parse(html)
    links: list[str] = []
    for link in tree.css("link[href]"):
        rel = (link.attributes.get("rel") or "").lower().split()
        kind = (link.attributes.get("type") or "").lower().strip()
        if "alternate" in rel and kind in _FEED_TYPES:
            url = urljoin(page_url, link.attributes["href"])
            if url not in links:
                links.append(url)
    return links


def page_title(html: bytes | str) -> str:
    tree = _parse(html)
    og = tree.css_first('meta[property="og:site_name"]')
    title = tree.css_first("title")
    if title is not None and _text(title):
        return _text(title)
    return (og.attributes.get("content") or "").strip() if og is not None else ""
