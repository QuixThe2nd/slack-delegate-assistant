"""Run the Winnie subagent through the claude or codex CLI."""

from __future__ import annotations

import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
PROMPT_FILE = ROOT / "prompts" / "winnie.md"

DEFAULT_TIMEOUT_SECONDS = 1800
MAX_ERROR_LENGTH = 300


class DelegateError(RuntimeError):
    """A Winnie runner failure safe to return to the tool caller."""


DelegateError = DelegateError


def _timeout() -> float | None:
    raw = os.environ.get("WINNIE_TIMEOUT_SECONDS")
    if raw is None or raw.strip() == "":
        return float(DEFAULT_TIMEOUT_SECONDS)
    try:
        value = float(raw.strip())
    except ValueError:
        raise DelegateError(f"WINNIE_TIMEOUT_SECONDS must be a number: {raw!r}") from None
    if not math.isfinite(value):
        raise DelegateError(f"WINNIE_TIMEOUT_SECONDS must be finite: {raw!r}")
    if value < 0:
        raise DelegateError(f"WINNIE_TIMEOUT_SECONDS must not be negative: {value}")
    return value or None


def host() -> str:
    configured = os.environ.get("WINNIE_HOST")
    if configured:
        chosen = configured.strip().lower()
        if chosen not in ("claude", "codex"):
            raise DelegateError(f"WINNIE_HOST must be claude or codex: {configured!r}")
        return chosen
    if os.environ.get("CLAUDE_PLUGIN_ROOT"):
        return "claude"
    if os.environ.get("PLUGIN_ROOT"):
        return "codex"
    if shutil.which("claude"):
        return "claude"
    if shutil.which("codex"):
        return "codex"
    raise DelegateError("No Winnie executable found: install claude or codex")


def _redact(text: str) -> str:
    token = os.environ.get("SLACK_BOT_TOKEN")
    if token:
        text = text.replace(token, "[redacted]")
    return text[:MAX_ERROR_LENGTH]


def _claude_argv(prompt: str) -> list[str]:
    return [
        "claude",
        "-p",
        "--bare",
        "--output-format",
        "text",
        "--system-prompt-file",
        str(PROMPT_FILE),
        "--allowed-tools",
        "WebSearch,WebFetch",
        "--disallowed-tools",
        "Bash,Edit,Write,NotebookEdit",
        prompt,
    ]


def _codex_argv(prompt: str, output_file: str) -> list[str]:
    persona = PROMPT_FILE.read_text(encoding="utf-8").strip()
    return [
        "codex",
        "exec",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "--output-last-message",
        output_file,
        f"{persona}\n\nTask:\n{prompt}",
    ]


def run(prompt: str) -> str:
    command = os.environ.get("WINNIE_COMMAND")
    output_file: str | None = None
    if command:
        argv = [command]
        stdin = None
    elif host() == "claude":
        argv = _claude_argv(prompt)
        stdin = subprocess.DEVNULL
    else:
        handle, output_file = tempfile.mkstemp(prefix="winnie-", suffix=".txt")
        os.close(handle)
        argv = _codex_argv(prompt, output_file)
        stdin = subprocess.DEVNULL

    try:
        try:
            if stdin is None:
                completed = subprocess.run(
                    argv,
                    input=prompt,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=_timeout(),
                )
            else:
                completed = subprocess.run(
                    argv,
                    stdin=stdin,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=_timeout(),
                )
        except subprocess.TimeoutExpired:
            raise DelegateError("Winnie timed out") from None
        except OSError:
            raise DelegateError(f"Winnie could not be started: {argv[0]}") from None
        if completed.returncode != 0:
            detail = _redact((completed.stderr or "") + (completed.stdout or ""))
            raise DelegateError(
                f"Winnie failed with exit code {completed.returncode}: {detail}"
            )
        if output_file is None:
            outcome = completed.stdout or ""
        else:
            with open(output_file, encoding="utf-8") as saved:
                outcome = saved.read()
    finally:
        if output_file is not None:
            try:
                os.unlink(output_file)
            except OSError:
                pass

    outcome = outcome.strip()
    if not outcome:
        raise DelegateError("Winnie returned an empty outcome")
    return outcome


run_winnie = run
