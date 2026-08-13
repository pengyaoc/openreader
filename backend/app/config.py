"""Config loading: config/feeds.yaml is the source of truth for sources,
regex rules, and LLM generation topics. Validated eagerly on load/save so a
bad edit never reaches the pipeline.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import msgspec
import yaml

RuleAction = Literal["include", "exclude"]
RuleField = Literal["title", "summary", "content", "author", "url", "any"]
SourceType = Literal["rss", "imap"]

# feeds.yaml is served verbatim, unauthenticated, by GET /api/config — so a
# credential ever written into it would be published at that URL. msgspec
# silently drops unknown struct fields (verified: extra keys like `password`
# don't raise), so this check runs against the raw parsed YAML in
# parse_config, before struct conversion would hide it.
_CREDENTIAL_KEY_RE = re.compile(r"(password|secret|token|api[_-]?key|credential)", re.IGNORECASE)


class ConfigError(Exception):
    """Raised when feeds.yaml fails validation. Carries a human-readable
    message pointing at the offending field so the UI can surface it."""


class Rule(msgspec.Struct, frozen=True):
    action: RuleAction
    field: RuleField
    pattern: str
    # Compiled lazily by compile_rules(); not part of the wire struct.


class CompiledRule(msgspec.Struct, frozen=True):
    action: RuleAction
    field: RuleField
    pattern: str
    regex: re.Pattern = msgspec.field(default=None)  # type: ignore[assignment]


class Source(msgspec.Struct, frozen=True, kw_only=True):
    key: str
    type: SourceType
    title: str
    folder: str
    url: str | None = None
    query: str | None = None
    mailbox_folder: str | None = None  # type=imap only; IMAP mailbox to SEARCH (default INBOX).
    # Distinct from `folder` above, which is a UI grouping label, not a mailbox.
    poll_interval_minutes: int | None = None
    fetch_full_text: bool = False
    rules: list[Rule] = msgspec.field(default_factory=list)


class Topic(msgspec.Struct, frozen=True, kw_only=True):
    key: str
    title: str
    folder: str
    brief: str
    lookback_days: int = 7
    max_articles: int = 3


class LLMSettings(msgspec.Struct, frozen=True, kw_only=True):
    enabled: bool = False
    model: str = "sonnet"
    timeout_minutes: int = 10


class Defaults(msgspec.Struct, frozen=True, kw_only=True):
    poll_interval_minutes: int = 30
    fetch_full_text: bool = False


class Config(msgspec.Struct, frozen=True, kw_only=True):
    interest_profile: str = ""
    llm: LLMSettings = msgspec.field(default_factory=LLMSettings)
    defaults: Defaults = msgspec.field(default_factory=Defaults)
    sources: list[Source] = msgspec.field(default_factory=list)
    topics: list[Topic] = msgspec.field(default_factory=list)

    def source(self, key: str) -> Source | None:
        return next((s for s in self.sources if s.key == key), None)

    def topic(self, key: str) -> Topic | None:
        return next((t for t in self.topics if t.key == key), None)


def _validate_rules(source_key: str, rules: list[Rule]) -> None:
    for rule in rules:
        try:
            re.compile(rule.pattern)
        except re.error as exc:
            raise ConfigError(
                f"source '{source_key}': invalid regex in rule "
                f"{rule.action}/{rule.field} pattern={rule.pattern!r}: {exc}"
            ) from exc


def validate_config(config: Config) -> None:
    seen_keys: set[str] = set()
    for source in config.sources:
        if source.key in seen_keys:
            raise ConfigError(f"duplicate source key: {source.key}")
        seen_keys.add(source.key)
        if source.type == "rss" and not source.url:
            raise ConfigError(f"source '{source.key}': type=rss requires 'url'")
        # type=imap has no required field: an absent query means "everything
        # in the mailbox folder", which is a reasonable default for a
        # dedicated newsletter-only account.
        _validate_rules(source.key, source.rules)

    topic_keys: set[str] = set()
    for topic in config.topics:
        if topic.key in topic_keys:
            raise ConfigError(f"duplicate topic key: {topic.key}")
        topic_keys.add(topic.key)


def _check_no_credentials(data: dict) -> None:
    """Rejects any source field that looks like a credential. Runs against
    the raw dict, not the parsed Struct — msgspec.convert silently drops
    unknown fields rather than erroring on them, so by the time a Config
    exists a smuggled `password:` would already be gone from view, not
    rejected. Credentials belong in environment variables (see README)."""
    for source in data.get("sources") or []:
        if not isinstance(source, dict):
            continue
        for key in source:
            if _CREDENTIAL_KEY_RE.search(str(key)):
                raise ConfigError(
                    f"source '{source.get('key', '?')}': field '{key}' looks like a "
                    "credential — feeds.yaml is served unauthenticated via GET "
                    "/api/config, so it must never hold one; use an environment "
                    "variable instead"
                )


def parse_config(raw_yaml: str) -> Config:
    """Parse and validate a feeds.yaml document. Raises ConfigError on any
    problem — missing required fields, bad regex, duplicate keys."""
    try:
        data = yaml.safe_load(raw_yaml) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML: {exc}") from exc

    _check_no_credentials(data)

    try:
        config = msgspec.convert(data, type=Config, strict=False)
    except msgspec.ValidationError as exc:
        raise ConfigError(f"invalid config: {exc}") from exc

    validate_config(config)
    return config


def _strip_none(obj):
    if isinstance(obj, dict):
        return {k: _strip_none(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [_strip_none(v) for v in obj]
    return obj


def to_yaml(config: Config) -> str:
    """Serializes a Config back to YAML text — used when the app itself
    mutates config (e.g. the structured 'Add source' UI) rather than the
    user hand-editing the file. None-valued optional fields are dropped so
    programmatic writes stay as readable as a hand-authored file."""
    raw = msgspec.to_builtins(config)
    cleaned = _strip_none(raw)
    return yaml.safe_dump(cleaned, allow_unicode=True, sort_keys=False, width=100)


def load_config(path: str | Path) -> Config:
    path = Path(path)
    return parse_config(path.read_text())


def compile_rule(rule: Rule) -> CompiledRule:
    return CompiledRule(
        action=rule.action,
        field=rule.field,
        pattern=rule.pattern,
        regex=re.compile(rule.pattern),
    )
