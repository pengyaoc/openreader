"""HTML-to-plain-text (for list-view excerpts) and HTML sanitization (for
anything rendered via dangerouslySetInnerHTML in the reader). Feed and
newsletter content is untrusted input regardless of source.
"""
from __future__ import annotations

import re
from urllib.parse import quote

import nh3
from selectolax.parser import HTMLParser

_ALLOWED_TAGS = {
    "p", "br", "b", "strong", "i", "em", "a", "img", "figure", "figcaption",
    "h1", "h2", "h3", "h4", "ul", "ol", "li", "blockquote", "pre", "code",
    "table", "thead", "tbody", "tr", "th", "td", "hr", "span",
}
_ALLOWED_ATTRIBUTES = {
    "a": {"href", "title"},
    "img": {"src", "alt", "title"},
}

# Auto-generated feeds (hnrss.org and similar) often emit a "description"
# that's pure link/metadata echo rather than a summary — e.g. "Article URL:
# https://…", "Comments URL: https://…", "Points: 176". That's not useful as
# a subtitle for deciding whether to read something, so it's filtered out of
# excerpts entirely rather than truncated into. Deterministic, not an LLM
# call — matches this app's constraint that RSS mode never uses one.
_BOILERPLATE_LINE = re.compile(
    r"^(article url|comments url|points|#\s*comments)\s*:?", re.IGNORECASE
)


def plain_text_excerpt(html: str | None, limit: int = 300) -> str:
    if not html:
        return ""
    tree = HTMLParser(html)
    blocks = tree.css("p, li") or [tree.body or tree.root]
    kept = []
    for block in blocks:
        text = " ".join(block.text(deep=True, separator=" ").split())
        if text and not _BOILERPLATE_LINE.match(text):
            kept.append(text)
    text = " ".join(kept).strip()
    return text[:limit]


def sanitize_html(html: str | None) -> str:
    if not html:
        return ""
    return nh3.clean(
        html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        # Every link in article/newsletter/generated content is third-party,
        # untrusted content — force it to open in a new tab rather than
        # navigating the reader away, regardless of what the source HTML
        # requested. nh3 already defaults rel="noopener noreferrer".
        set_tag_attribute_values={"a": {"target": "_blank"}},
    )


def _is_visually_empty(node) -> bool:
    text = node.text(deep=True, separator="").replace("\xa0", "").strip()
    return not text and node.css_first("img") is None


def tighten_newsletter_whitespace(html: str | None) -> str:
    """Collapses the excessive blank space common in HTML newsletter markup:
    empty <p> spacer paragraphs (e.g. `<p>&nbsp;</p>`, used by email builders
    for vertical padding that CSS would normally handle), runs of consecutive
    <br> tags, runs of consecutive &nbsp; (email builders pad inbox
    preview-snippet length with dozens of them, usually inside a hidden
    element — the hiding mechanism is CSS we strip, so left uncollapsed it
    surfaces as a wall of visible spaces mid-paragraph), empty spacer <tr>
    rows, and empty spacer <table> chains.

    Email builders lay out a newsletter as one (or a few) layout tables
    with real content rows and empty `<tr><td> </td></tr>` spacer rows
    interleaved in the *same* table — found live, 2026-08-13, on a WSJ
    newsletter article: 81 of 179 <tr> elements were pure spacers, none of
    which the table-level check below ever caught, since the enclosing
    table wasn't empty overall (it had real content in its other rows).
    Each <tr> renders as its own block box once the reader's CSS makes
    tables scrollable, so left in place those spacer rows alone produced
    a wall of blank lines — the dominant cause, not the fully-empty nested
    spacer tables (5-10 levels deep, around nothing but a stray &nbsp;)
    the table sweep below still also removes. Applied only to
    email-sourced content — RSS feeds don't share this markup pattern."""
    if not html:
        return html or ""
    tree = HTMLParser(f"<body>{html}</body>")
    for p in tree.css("p"):
        if _is_visually_empty(p):
            p.decompose()
    # Bottom-up, to a fixed point: removing an innermost empty spacer row
    # can leave its enclosing table empty too, and removing a table can
    # leave an outer row (in a parent layout table) empty in turn — sweep
    # both together until a pass removes nothing.
    removed = True
    while removed:
        removed = False
        for tr in tree.css("tr"):
            if _is_visually_empty(tr):
                tr.decompose()
                removed = True
        for table in tree.css("table"):
            if _is_visually_empty(table):
                table.decompose()
                removed = True
    result = tree.body.html[len("<body>") : -len("</body>")] if tree.body else html
    result = re.sub(r"(?:\s*<br\s*/?>\s*){2,}", "<br>", result)
    result = re.sub(r"(?:&nbsp;\s*){3,}", " ", result)
    return result


def proxy_image_urls(html: str | None) -> str:
    """Sanitizes and rewrites every <img src> to go through /api/img — many
    sites (dapenti.com's image CDN among them) hotlink-protect on Referer,
    which breaks direct <img src> loads from our own origin. Routing through
    our server sidesteps that and avoids leaking the reader's IP/referrer to
    third-party image hosts on every article view."""
    clean = sanitize_html(html)
    if not clean:
        return clean
    tree = HTMLParser(clean)
    for img in tree.css("img"):
        src = img.attributes.get("src")
        if src:
            img.attrs["src"] = f"/api/img?url={quote(src, safe='')}"
    return tree.body.html[len("<body>") : -len("</body>")] if tree.body else clean
