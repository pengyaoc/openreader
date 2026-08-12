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
