"""Tests for the claude CLI wrapper. The subprocess call itself is injected
(no real subprocess spawned in tests) — these verify command construction
and response-envelope parsing, including every failure mode.
"""
import json
import subprocess

import pytest

from app.generate.client import ClaudeError, build_command, generate_articles


def test_build_command_never_passes_bare():
    cmd = build_command(model="sonnet", json_schema={"type": "object"})
    assert "--bare" not in cmd


def test_build_command_uses_strict_mcp_config_and_empty_setting_sources():
    cmd = build_command(model="sonnet", json_schema={"type": "object"})
    assert "--strict-mcp-config" in cmd
    idx = cmd.index("--setting-sources")
    assert cmd[idx + 1] == ""


def test_build_command_grants_only_research_tools():
    cmd = build_command(model="sonnet", json_schema={"type": "object"})
    idx = cmd.index("--tools")
    assert cmd[idx + 1] == "WebSearch,WebFetch"


def test_build_command_uses_requested_model():
    cmd = build_command(model="sonnet", json_schema={"type": "object"})
    idx = cmd.index("--model")
    assert cmd[idx + 1] == "sonnet"


def test_build_command_uses_bypass_permissions_not_dont_ask():
    # Verified live: --permission-mode dontAsk silently *denies* every
    # WebSearch/WebFetch call in headless mode (visible in the response's
    # permission_denials array) and the run completes anyway with zero
    # research done. bypassPermissions is what actually lets the two
    # research-only tools --tools already restricts to run.
    cmd = build_command(model="sonnet", json_schema={"type": "object"})
    idx = cmd.index("--permission-mode")
    assert cmd[idx + 1] == "bypassPermissions"


def make_completed(stdout: str, returncode: int = 0, stderr: str = ""):
    return subprocess.CompletedProcess(args=["claude"], returncode=returncode, stdout=stdout, stderr=stderr)


def test_generate_articles_returns_structured_output_articles():
    envelope = json.dumps(
        {
            "is_error": False,
            "structured_output": {
                "articles": [
                    {
                        "title": "Test",
                        "summary": "s",
                        "body_html": "<p>x</p>",
                        "sources": [{"title": "Src", "url": "https://x.com"}],
                    }
                ]
            },
        }
    )
    runner = lambda cmd, stdin, timeout: make_completed(envelope)

    articles = generate_articles("brief", runner=runner)

    assert len(articles) == 1
    assert articles[0]["title"] == "Test"


def test_default_runner_scrubs_anthropic_api_key_from_child_env(monkeypatch):
    # The injectable Runner interface used elsewhere in these tests doesn't
    # carry env — that's a concern of the real subprocess call only. Verify
    # it by patching subprocess.run itself and checking what it was given.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-be-passed")
    captured = {}

    def fake_run(cmd, input, capture_output, text, timeout, env):
        captured["env"] = env
        return make_completed(
            json.dumps({"is_error": False, "structured_output": {"articles": []}})
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    from app.generate.client import _default_runner

    generate_articles("brief", runner=_default_runner)
    assert "ANTHROPIC_API_KEY" not in captured["env"]


def test_generate_articles_raises_on_nonzero_exit():
    runner = lambda cmd, stdin, timeout: make_completed("", returncode=1, stderr="boom")
    with pytest.raises(ClaudeError, match="boom"):
        generate_articles("brief", runner=runner)


def test_generate_articles_raises_when_is_error_true():
    envelope = json.dumps({"is_error": True, "result": "something went wrong"})
    runner = lambda cmd, stdin, timeout: make_completed(envelope)
    with pytest.raises(ClaudeError, match="something went wrong"):
        generate_articles("brief", runner=runner)


def test_generate_articles_raises_on_malformed_json():
    runner = lambda cmd, stdin, timeout: make_completed("not json at all")
    with pytest.raises(ClaudeError):
        generate_articles("brief", runner=runner)


def test_generate_articles_raises_when_structured_output_missing():
    envelope = json.dumps({"is_error": False})
    runner = lambda cmd, stdin, timeout: make_completed(envelope)
    with pytest.raises(ClaudeError, match="structured_output"):
        generate_articles("brief", runner=runner)


def test_generate_articles_returns_empty_list_when_model_found_nothing_new():
    envelope = json.dumps({"is_error": False, "structured_output": {"articles": []}})
    runner = lambda cmd, stdin, timeout: make_completed(envelope)
    assert generate_articles("brief", runner=runner) == []


def test_generate_articles_raises_on_timeout():
    def runner(cmd, stdin, timeout):
        raise subprocess.TimeoutExpired(cmd, timeout)

    with pytest.raises(ClaudeError, match="timed out"):
        generate_articles("brief", runner=runner, timeout=1.0)
