"""Tests for the summarization CLI wrapper — mirrors
test_generate_client.py's conventions: the subprocess call is injected, no
real `claude` process spawned here.
"""
import json
import subprocess

import pytest

from app.generate.client import ClaudeError
from app.generate.summarize import build_summarize_command, build_user_prompt, summarize_text


def test_build_summarize_command_never_passes_bare():
    cmd = build_summarize_command(model="sonnet")
    assert "--bare" not in cmd


def test_build_summarize_command_uses_strict_mcp_config_and_empty_setting_sources():
    cmd = build_summarize_command(model="sonnet")
    assert "--strict-mcp-config" in cmd
    idx = cmd.index("--setting-sources")
    assert cmd[idx + 1] == ""


def test_build_summarize_command_disables_all_tools():
    cmd = build_summarize_command(model="sonnet")
    idx = cmd.index("--tools")
    assert cmd[idx + 1] == ""


def test_build_summarize_command_uses_requested_model():
    cmd = build_summarize_command(model="sonnet")
    idx = cmd.index("--model")
    assert cmd[idx + 1] == "sonnet"


@pytest.mark.parametrize(
    "word_count,expected_target",
    [
        (200, 100),  # max(100, 40) = 100
        (1000, 200),  # max(100, 200) = 200
        (50, 100),  # max(100, 10) = 100
    ],
)
def test_build_user_prompt_states_proportional_minimum_word_target(word_count, expected_target):
    prompt = build_user_prompt("some article text", word_count)
    assert f"at least {expected_target} words" in prompt
    assert f"{word_count} words" in prompt


def make_completed(stdout: str, returncode: int = 0, stderr: str = ""):
    return subprocess.CompletedProcess(args=["claude"], returncode=returncode, stdout=stdout, stderr=stderr)


def test_summarize_text_returns_sanitized_summary_html():
    envelope = json.dumps(
        {
            "is_error": False,
            "structured_output": {"summary_html": "<ul><li>Point one</li></ul>"},
        }
    )
    runner = lambda cmd, stdin, timeout: make_completed(envelope)

    summary = summarize_text("article text", word_count=500, runner=runner)

    assert summary == "<ul><li>Point one</li></ul>"


def test_summarize_text_strips_disallowed_tags_from_model_output():
    envelope = json.dumps(
        {
            "is_error": False,
            "structured_output": {
                "summary_html": "<p>Safe</p><script>alert(1)</script>"
            },
        }
    )
    runner = lambda cmd, stdin, timeout: make_completed(envelope)

    summary = summarize_text("article text", word_count=500, runner=runner)

    assert "<script>" not in summary
    assert "<p>Safe</p>" in summary


def test_summarize_text_raises_on_nonzero_exit():
    runner = lambda cmd, stdin, timeout: make_completed("", returncode=1, stderr="boom")
    with pytest.raises(ClaudeError, match="boom"):
        summarize_text("article text", word_count=500, runner=runner)


def test_summarize_text_raises_when_is_error_true():
    envelope = json.dumps({"is_error": True, "result": "something went wrong"})
    runner = lambda cmd, stdin, timeout: make_completed(envelope)
    with pytest.raises(ClaudeError, match="something went wrong"):
        summarize_text("article text", word_count=500, runner=runner)


def test_summarize_text_raises_on_malformed_json():
    runner = lambda cmd, stdin, timeout: make_completed("not json at all")
    with pytest.raises(ClaudeError):
        summarize_text("article text", word_count=500, runner=runner)


def test_summarize_text_raises_when_structured_output_missing():
    envelope = json.dumps({"is_error": False})
    runner = lambda cmd, stdin, timeout: make_completed(envelope)
    with pytest.raises(ClaudeError, match="summary_html"):
        summarize_text("article text", word_count=500, runner=runner)


def test_summarize_text_raises_on_timeout():
    def runner(cmd, stdin, timeout):
        raise subprocess.TimeoutExpired(cmd, timeout)

    with pytest.raises(ClaudeError, match="timed out"):
        summarize_text("article text", word_count=500, runner=runner, timeout=1.0)
