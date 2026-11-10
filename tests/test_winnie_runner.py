from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from server.winnie_runner import DelegateError, run_winnie


ROOT = Path(__file__).resolve().parents[1]
TEST_TOKEN = "runner-test-token"


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in (
        "WINNIE_HOST",
        "WINNIE_COMMAND",
        "WINNIE_TIMEOUT_SECONDS",
        "CLAUDE_PLUGIN_ROOT",
        "PLUGIN_ROOT",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SLACK_BOT_TOKEN", TEST_TOKEN)


@pytest.fixture(autouse=True)
def run_process(monkeypatch):
    mocked = Mock()

    def succeed(argv, **kwargs):
        if argv[0] == "codex":
            output_path = Path(argv[argv.index("--output-last-message") + 1])
            output_path.write_text("Winnie outcome\n", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="Winnie outcome\n", stderr="")

    mocked.side_effect = succeed
    monkeypatch.setattr("server.winnie_runner.subprocess.run", mocked)
    return mocked


def test_custom_command_is_one_executable_using_stdin_and_default_timeout(
    monkeypatch, run_process
):
    command = "/tmp/fake winnie executable"
    monkeypatch.setenv("WINNIE_COMMAND", command)
    monkeypatch.setenv("WINNIE_HOST", "not-a-host")

    assert run_winnie("Investigate this") == "Winnie outcome"

    argv = run_process.call_args.args[0]
    kwargs = run_process.call_args.kwargs
    assert argv == [command]
    assert kwargs["input"] == "Investigate this"
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True
    assert kwargs["check"] is False
    assert kwargs["timeout"] == 1800
    assert kwargs.get("shell", False) is False


def test_claude_argv_matches_contract(monkeypatch, run_process):
    monkeypatch.setenv("WINNIE_HOST", "claude")

    assert run_winnie("Research the change") == "Winnie outcome"

    argv = run_process.call_args.args[0]
    assert argv == [
        "claude",
        "-p",
        "--bare",
        "--output-format",
        "text",
        "--system-prompt-file",
        str(ROOT / "prompts" / "winnie.md"),
        "--allowed-tools",
        "WebSearch,WebFetch",
        "--disallowed-tools",
        "Bash,Edit,Write,NotebookEdit",
        "Research the change",
    ]
    kwargs = run_process.call_args.kwargs
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["timeout"] == 1800
    assert kwargs.get("shell", False) is False


def test_codex_argv_contains_persona_task_and_prompt(monkeypatch, run_process):
    monkeypatch.setenv("WINNIE_HOST", "codex")

    assert run_winnie("Research the change") == "Winnie outcome"

    argv = run_process.call_args.args[0]
    assert argv[:7] == [
        "codex",
        "exec",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "--output-last-message",
        argv[6],
    ]
    assert argv[6]
    persona = (ROOT / "prompts" / "winnie.md").read_text(encoding="utf-8").strip()
    assert argv[7:] == [f"{persona}\n\nTask:\nResearch the change"]
    kwargs = run_process.call_args.kwargs
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs.get("shell", False) is False


@pytest.mark.parametrize(
    ("explicit_host", "claude_root", "plugin_root", "available", "expected"),
    [
        ("codex", "/claude", "/codex", {"claude", "codex"}, "codex"),
        (None, "/claude", "/codex", {"codex"}, "claude"),
        (None, None, "/codex", {"claude"}, "codex"),
        (None, None, None, {"codex"}, "codex"),
        (None, None, None, {"claude", "codex"}, "claude"),
    ],
)
def test_host_selection_precedence(
    monkeypatch,
    run_process,
    explicit_host,
    claude_root,
    plugin_root,
    available,
    expected,
):
    if explicit_host is not None:
        monkeypatch.setenv("WINNIE_HOST", explicit_host)
    if claude_root is not None:
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", claude_root)
    if plugin_root is not None:
        monkeypatch.setenv("PLUGIN_ROOT", plugin_root)
    monkeypatch.setattr(
        "server.winnie_runner.shutil.which",
        lambda name: f"/usr/bin/{name}" if name in available else None,
    )

    assert run_winnie("Task") == "Winnie outcome"
    assert run_process.call_args.args[0][0] == expected


def test_invalid_host_starts_nothing(monkeypatch, run_process):
    monkeypatch.setenv("WINNIE_HOST", "other")

    with pytest.raises(DelegateError, match="WINNIE_HOST"):
        run_winnie("Task")

    run_process.assert_not_called()


def test_missing_host_binaries_start_nothing(monkeypatch, run_process):
    monkeypatch.setattr("server.winnie_runner.shutil.which", lambda name: None)

    with pytest.raises(DelegateError, match="claude|codex|executable"):
        run_winnie("Task")

    run_process.assert_not_called()


@pytest.mark.parametrize(("setting", "expected"), [("0", None), ("12.5", 12.5)])
def test_timeout_environment(monkeypatch, run_process, setting, expected):
    monkeypatch.setenv("WINNIE_COMMAND", "/tmp/fake-winnie")
    monkeypatch.setenv("WINNIE_TIMEOUT_SECONDS", setting)

    run_winnie("Task")

    assert run_process.call_args.kwargs["timeout"] == expected


@pytest.mark.parametrize("setting", ["-1", "invalid", "nan", "inf"])
def test_invalid_timeout_starts_nothing(monkeypatch, run_process, setting):
    monkeypatch.setenv("WINNIE_COMMAND", "/tmp/fake-winnie")
    monkeypatch.setenv("WINNIE_TIMEOUT_SECONDS", setting)

    with pytest.raises(DelegateError, match="WINNIE_TIMEOUT_SECONDS"):
        run_winnie("Task")

    run_process.assert_not_called()


def test_nonzero_exit_is_sanitized(monkeypatch, run_process):
    monkeypatch.setenv("WINNIE_COMMAND", "/tmp/fake-winnie")
    run_process.side_effect = None
    run_process.return_value = SimpleNamespace(
        returncode=7,
        stdout="",
        stderr=f"secret: {TEST_TOKEN}",
    )

    with pytest.raises(DelegateError, match="7") as caught:
        run_winnie("Task")

    assert TEST_TOKEN not in str(caught.value)


def test_timeout_is_sanitized(monkeypatch, run_process):
    monkeypatch.setenv("WINNIE_COMMAND", "/tmp/fake-winnie")
    run_process.side_effect = subprocess.TimeoutExpired(cmd=TEST_TOKEN, timeout=1800)

    with pytest.raises(DelegateError, match="timed out") as caught:
        run_winnie("Task")

    assert TEST_TOKEN not in str(caught.value)


@pytest.mark.parametrize("host", ["custom", "claude", "codex"])
def test_empty_outcome_raises(monkeypatch, run_process, host):
    if host == "custom":
        monkeypatch.setenv("WINNIE_COMMAND", "/tmp/fake-winnie")
    else:
        monkeypatch.setenv("WINNIE_HOST", host)

    def empty_result(argv, **kwargs):
        if argv[0] == "codex":
            output_path = Path(argv[argv.index("--output-last-message") + 1])
            output_path.write_text(" \n", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout=" \n", stderr="")

    run_process.side_effect = empty_result

    with pytest.raises(DelegateError, match="empty outcome"):
        run_winnie("Task")


def test_start_failure_does_not_leak_token(monkeypatch, run_process):
    monkeypatch.setenv("WINNIE_COMMAND", "/tmp/fake-winnie")
    run_process.side_effect = OSError(TEST_TOKEN)

    with pytest.raises(DelegateError, match="could not be started") as caught:
        run_winnie("Task")

    assert TEST_TOKEN not in str(caught.value)
