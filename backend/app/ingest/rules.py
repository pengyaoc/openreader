"""Regex rule engine. Pure — no I/O, no config parsing (that's app.config).

Semantics (from the design doc):
  - Rules are evaluated in the order given.
  - Any matching `exclude` rule drops the article immediately; the first
    matching exclude is reported as `matched`.
  - If the source has >=1 `include` rule, the article must match at least
    one to pass. With no include rules, everything not excluded passes.
  - field="any" ORs the pattern across title/summary/content/author/url.
"""
from __future__ import annotations

import msgspec

from app.config import CompiledRule


class RawArticle(msgspec.Struct, kw_only=True):
    title: str = ""
    summary: str = ""
    content: str = ""
    author: str = ""
    url: str = ""


_FIELDS = ("title", "summary", "content", "author", "url")


def _field_value(article: RawArticle, field: str) -> str:
    return getattr(article, field, "")


def _rule_matches(article: RawArticle, rule: CompiledRule) -> bool:
    if rule.field == "any":
        return any(rule.regex.search(_field_value(article, f)) for f in _FIELDS)
    return bool(rule.regex.search(_field_value(article, rule.field)))


def evaluate_rules(
    article: RawArticle, rules: list[CompiledRule]
) -> tuple[bool, CompiledRule | None]:
    """Returns (passed, matched_rule). matched_rule is the rule that decided
    the outcome — the first matching exclude, or the first matching include
    when that's what let the article through."""
    include_rules = [r for r in rules if r.action == "include"]

    for rule in rules:
        if rule.action == "exclude" and _rule_matches(article, rule):
            return False, rule

    if not include_rules:
        return True, None

    for rule in include_rules:
        if _rule_matches(article, rule):
            return True, rule

    return False, None
