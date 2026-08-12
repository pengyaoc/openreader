"""System prompt and output schema for LLM article generation.

The model researches a topic brief with WebSearch/WebFetch and writes one
or more short articles. Output is validated against ARTICLES_JSON_SCHEMA via
`claude --json-schema`, so the response envelope's structured_output field
arrives already parsed — no JSON-from-prose extraction needed.
"""
from __future__ import annotations

GENERATOR_SYSTEM_PROMPT = """You are a research writer for a personal RSS reader app.

You will be given a topic brief describing what the reader wants to track —
something with no good RSS feed of its own, or scattered across many sources.
Research it using WebSearch and WebFetch, then write one or more short
articles covering genuinely new, concrete developments (not evergreen
background). Favor primary sources over aggregator commentary.

Rules:
- Every claim must trace back to a real source you actually found. Never
  fabricate a fact, a quote, or a URL.
- Always include a `sources` list with real, working URLs.
- `body_html` may only use these tags: p, br, b, strong, i, em, a, ul, ol,
  li, blockquote, h2, h3. No images, no scripts, no styling.
- If you find nothing genuinely new since a reasonable lookback window,
  return an empty `articles` array rather than padding with stale material.
- Output must match the provided JSON schema exactly."""

ARTICLES_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "articles": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "summary": {"type": "string"},
                    "body_html": {"type": "string"},
                    "sources": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "url": {"type": "string"},
                            },
                            "required": ["title", "url"],
                        },
                    },
                },
                "required": ["title", "summary", "body_html", "sources"],
            },
        }
    },
    "required": ["articles"],
}


def build_user_prompt(brief: str, lookback_days: int, max_articles: int) -> str:
    return (
        f"Topic brief:\n{brief}\n\n"
        f"Lookback window: only cover developments from the last {lookback_days} days.\n"
        f"Write at most {max_articles} article(s). Return fewer (or zero) if that's "
        f"all the genuinely new material supports."
    )
