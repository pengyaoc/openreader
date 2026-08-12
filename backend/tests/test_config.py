"""Tests for app.config: YAML loading, validation, and write-back."""
import pytest

from app.config import ConfigError, Rule, Source, load_config, parse_config, to_yaml


def test_load_valid_minimal_config(tmp_path):
    p = tmp_path / "feeds.yaml"
    p.write_text(
        """
sources:
  - key: example
    type: rss
    title: Example
    url: https://example.com/feed.xml
    folder: Test
"""
    )
    config = load_config(p)
    assert len(config.sources) == 1
    assert config.sources[0].key == "example"
    assert config.sources[0].type == "rss"
    assert config.sources[0].url == "https://example.com/feed.xml"


def test_missing_required_field_is_rejected(tmp_path):
    p = tmp_path / "feeds.yaml"
    p.write_text(
        """
sources:
  - key: example
    type: rss
    folder: Test
"""
    )
    with pytest.raises(ConfigError):
        load_config(p)


def test_invalid_regex_in_rule_is_rejected(tmp_path):
    p = tmp_path / "feeds.yaml"
    p.write_text(
        """
sources:
  - key: example
    type: rss
    title: Example
    url: https://example.com/feed.xml
    folder: Test
    rules:
      - { action: exclude, field: title, pattern: "(unclosed" }
"""
    )
    with pytest.raises(ConfigError, match="regex"):
        load_config(p)


def test_to_yaml_roundtrips_through_parse_config():
    original = parse_config(
        """
sources:
  - key: example
    type: rss
    title: Example
    url: https://example.com/feed.xml
    folder: Test
    rules:
      - { action: include, field: title, pattern: "(?i)llm" }
"""
    )
    dumped = to_yaml(original)
    reparsed = parse_config(dumped)
    assert reparsed == original


def test_to_yaml_omits_none_fields_for_readability():
    config = parse_config(
        """
sources:
  - key: example
    type: rss
    title: Example
    url: https://example.com/feed.xml
    folder: Test
"""
    )
    dumped = to_yaml(config)
    # 'query' is a gmail-only field and unset here; 'defaults' legitimately
    # carries its own poll_interval_minutes, but the per-source override
    # (also named poll_interval_minutes, left unset on this source) should
    # not appear a second time inside the sources list.
    assert "query:" not in dumped
    assert dumped.count("poll_interval_minutes:") == 1


def test_to_yaml_preserves_appended_source():
    original = parse_config("sources: []")
    new_source = Source(
        key="new-src",
        type="rss",
        title="New Source",
        folder="Test",
        url="https://x.com/feed",
        rules=[Rule(action="exclude", field="title", pattern="(?i)ad")],
    )
    import msgspec

    updated = msgspec.structs.replace(original, sources=[new_source])
    dumped = to_yaml(updated)
    reparsed = parse_config(dumped)
    assert len(reparsed.sources) == 1
    assert reparsed.sources[0].key == "new-src"
    assert reparsed.sources[0].rules[0].pattern == "(?i)ad"
