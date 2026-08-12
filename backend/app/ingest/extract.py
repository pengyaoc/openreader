"""Compact readability extraction: given a full page, find the element most
likely to be the article body and discard nav/ad/footer chrome.

Not a full Readability.js port — a density heuristic that's good enough for
blog/news pages: strip known-noise tags outright, then score each remaining
container by paragraph text length and pick the highest scorer.
"""
from __future__ import annotations

from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from app.ingest.textutil import sanitize_html

_NOISE_TAGS = ("nav", "header", "footer", "aside", "script", "style", "form", "iframe")
_CANDIDATE_SELECTOR = "article, main, div, section"
_MIN_PARAGRAPH_LEN = 40


def _score(node) -> int:
    score = 0
    for p in node.css("p"):
        text = p.text(deep=True, separator=" ").strip()
        if len(text) >= _MIN_PARAGRAPH_LEN:
            score += len(text)
    return score


def _resolve_urls(node, base_url: str) -> None:
    """Rewrites relative src/href attributes to absolute URLs against the
    article's own page — otherwise they'd resolve against our app's origin
    once rendered in the reader, which is always wrong."""
    for img in node.css("img"):
        src = img.attributes.get("src")
        if src:
            img.attrs["src"] = urljoin(base_url, src)
    for a in node.css("a"):
        href = a.attributes.get("href")
        if href:
            a.attrs["href"] = urljoin(base_url, href)


def extract_readable(html: str, base_url: str | None = None) -> str:
    """Returns sanitized HTML of the best-guess main content block, or ''
    if no plausible article body is found. When base_url is given, relative
    image/link URLs are resolved against it."""
    tree = HTMLParser(html)
    if tree.body is None:
        return ""

    for tag in _NOISE_TAGS:
        for node in tree.css(tag):
            node.decompose()

    best = None
    best_score = 0
    for candidate in tree.css(_CANDIDATE_SELECTOR):
        score = _score(candidate)
        if score > best_score:
            best = candidate
            best_score = score

    if best is None or best_score == 0:
        return ""

    if base_url:
        _resolve_urls(best, base_url)

    return sanitize_html(best.html or "")
