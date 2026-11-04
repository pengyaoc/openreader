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
    # width: the source's declared display width, normalized to plain px
    # by _apply_inline_presentation (the reader CSS caps it at 100%).
    "img": {"src", "alt", "title", "width"},
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

# List-view subtitle length. Long enough to convey whether an article is
# worth opening, without turning each feed row into the article itself.
EXCERPT_LIMIT = 900


def plain_text_excerpt(html: str | None, limit: int | None = 300) -> str:
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
    return text if limit is None else text[:limit]


# Inline-style declarations that mean "this element is not shown". Email
# templates rely on these heavily: the inbox preheader (a duplicate of the
# standfirst, padded with &nbsp;), dark-mode variants of logos/icons that a
# <style> media query swaps in, and Outlook-only (`mso-hide`) fallbacks.
# Sanitizing strips `style`, which would otherwise turn every one of them
# visible — found live on a WSJ newsletter, 2026-11-03 (duplicated
# standfirst, a second washed-out masthead, stray arrow icons).
# `font-size:0` is deliberately not here: email builders put it on layout
# cells purely to collapse inline whitespace, with visible content inside.
_HIDDEN_STYLE = re.compile(
    r"(?:^|;)\s*(?:"
    r"display\s*:\s*none"
    r"|visibility\s*:\s*hidden"
    r"|mso-hide\s*:\s*all"
    r"|opacity\s*:\s*0(?:\.0*)?\s*(?:!important)?\s*(?:;|$)"
    r"|max-height\s*:\s*0(?:px)?\s*(?:!important)?\s*(?:;|$)"
    r")",
    re.IGNORECASE,
)
_STYLE_PX = re.compile(r"(?:^|;)\s*(max-width|width)\s*:\s*(\d+(?:\.\d+)?)px", re.IGNORECASE)

# At or below this declared width, an image is an inline icon (a link arrow,
# a social glyph), not a figure — rendered inline rather than as a block.
ICON_MAX_WIDTH = 48


def _declared_img_width(img) -> int | None:
    """The display width the source asked for, in CSS px: the `width`
    attribute if it's a plain number, else a px `width`/`max-width` from
    inline style. Percent/auto widths say nothing about intended size."""
    attr = (img.attributes.get("width") or "").strip()
    if attr.isdigit():
        return int(attr)
    found = {k.lower(): float(v) for k, v in _STYLE_PX.findall(img.attributes.get("style") or "")}
    value = found.get("width", found.get("max-width"))
    return int(value) if value else None


def _apply_inline_presentation(html: str) -> str:
    """Carries over the parts of inline presentation that sanitizing would
    otherwise lose but that change what the reader should see: drops
    elements the source hid, and pins each <img> to the width the source
    declared. Without the latter a 2x-retina 184px logo renders at its
    full natural width — the masthead blowup newsletters showed. Tracking
    pixels (≤2px) are dropped outright. Applies to every source: a feed
    that hides something inline meant it hidden too."""
    if "style" not in html and "hidden" not in html and "<img" not in html:
        return html
    tree = HTMLParser(f"<body>{html}</body>")
    for node in tree.css("[style], [hidden]"):
        attrs = node.attributes
        if "hidden" in attrs or _HIDDEN_STYLE.search(attrs.get("style") or ""):
            node.decompose()
    for img in tree.css("img"):
        width = _declared_img_width(img)
        height = (img.attributes.get("height") or "").strip()
        if (width is not None and width <= 2) or (height.isdigit() and int(height) <= 2):
            img.decompose()
            continue
        for attr in ("width", "height"):
            if attr in img.attributes:
                del img.attrs[attr]
        if width:
            img.attrs["width"] = str(width)
    return _body_inner_html(tree, html)


def sanitize_html(html: str | None) -> str:
    if not html:
        return ""
    return nh3.clean(
        _apply_inline_presentation(html),
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        # Every link in article/newsletter/generated content is third-party,
        # untrusted content — force it to open in a new tab rather than
        # navigating the reader away, regardless of what the source HTML
        # requested. nh3 already defaults rel="noopener noreferrer".
        set_tag_attribute_values={"a": {"target": "_blank"}},
    )


def _body_inner_html(tree: HTMLParser, fallback: str) -> str:
    """The <body>'s contents, without the <body> tag itself. Slicing a fixed
    "<body>" prefix off body.html broke on full email documents: their own
    <body style=...> merges its attributes into the wrapper, leaving a
    `style="..."` fragment as visible text at the top of the article."""
    if not tree.body:
        return fallback
    return "".join(child.html or "" for child in tree.body.iter(include_text=True))


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
    result = _body_inner_html(tree, html)
    result = re.sub(r"(?:\s*<br\s*/?>\s*){2,}", "<br>", result)
    result = re.sub(r"(?:&nbsp;\s*){3,}", " ", result)
    return result


def proxy_image_urls(html: str | None) -> str:
    """Sanitizes and rewrites every <img src> to go through /api/img — many
    sites (dapenti.com's image CDN among them) hotlink-protect on Referer,
    which breaks direct <img src> loads from our own origin. Routing through
    our server sidesteps that and avoids leaking the reader's IP/referrer to
    third-party image hosts on every article view.

    Every image after the first is also marked loading="lazy" (2026-10-27):
    opening an article otherwise fetched all of its images through the
    proxy at once, including ones far below the fold. The first is usually
    in view on open, where lazy loading would only delay it. Added after
    sanitizing, since nh3's allowlist would strip them."""
    clean = sanitize_html(html)
    if not clean:
        return clean
    tree = HTMLParser(clean)
    for i, img in enumerate(tree.css("img")):
        src = img.attributes.get("src")
        if src:
            img.attrs["src"] = f"/api/img?url={quote(src, safe='')}"
        if i > 0:
            img.attrs["loading"] = "lazy"
        width = img.attributes.get("width")
        if width and width.isdigit() and int(width) <= ICON_MAX_WIDTH:
            img.attrs["class"] = "icon"
        img.attrs["decoding"] = "async"
    return _body_inner_html(tree, clean)
