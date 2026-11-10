import json
import os
from pathlib import Path
import sys
from typing import Any
import urllib.error
import urllib.request

from mcp.server.fastmcp import FastMCP


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.winnie_runner import DelegateError, run_winnie


DEFAULT_SLACK_API_URL = "https://slack.com/api/chat.postMessage"

mcp = FastMCP("slack-delegate-assistant")


def _task(prompt: str | None, goal: str | None) -> str:
    value = prompt if prompt is not None else goal
    name = "prompt" if prompt is not None else "goal"
    if value is None:
        raise DelegateError("prompt must contain the task; goal is only an alias")
    if not isinstance(value, str):
        raise DelegateError(f"{name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise DelegateError("task must not be empty; pass it in prompt")
    return normalized


def _channel(chat_id: str | None) -> str | None:
    if chat_id is None:
        value = os.environ.get("SLACK_DEFAULT_CHANNEL")
        if value is None or not value.strip():
            return None
    else:
        value = chat_id
    if not isinstance(value, str):
        raise DelegateError("chat_id must be a string")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise DelegateError("chat_id must not contain ASCII control characters")
    value = value.strip()
    if not value:
        raise DelegateError("chat_id must not be empty")
    return value


def _decode_response(body: bytes) -> Any:
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def _slack_error(payload: Any, token: str) -> str | None:
    if not isinstance(payload, dict) or not isinstance(payload.get("error"), str):
        return None
    error = payload["error"].strip().replace(token, "[redacted]")
    error = " ".join(error.splitlines())
    return error[:200] or None


def _post_outcome(channel: str, outcome: str) -> None:
    token = os.environ.get("SLACK_BOT_TOKEN", "").strip()
    if not token:
        raise DelegateError("SLACK_BOT_TOKEN is required when a Slack destination is set")

    body = json.dumps(
        {
            "channel": channel,
            "text": outcome,
            "unfurl_links": False,
            "unfurl_media": False,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        os.environ.get("SLACK_API_URL") or DEFAULT_SLACK_API_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            status = getattr(response, "status", 200)
            response_body = response.read()
    except urllib.error.HTTPError as error:
        payload = _decode_response(error.read())
        slack_error = _slack_error(payload, token)
        suffix = f": {slack_error}" if slack_error else ""
        raise DelegateError(f"Slack HTTP {error.code}{suffix}") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise DelegateError("Slack request failed") from None

    payload = _decode_response(response_body)
    if payload is None:
        raise DelegateError("Slack returned invalid JSON")
    if not 200 <= status < 300:
        slack_error = _slack_error(payload, token)
        suffix = f": {slack_error}" if slack_error else ""
        raise DelegateError(f"Slack HTTP {status}{suffix}")
    if not isinstance(payload, dict):
        raise DelegateError("Slack returned an invalid response")
    if payload.get("ok") is not True:
        slack_error = _slack_error(payload, token) or "unknown_error"
        raise DelegateError(f"Slack error: {slack_error}")


@mcp.tool(name="delegate_assistant")
def delegate_assistant(
    prompt: str | None = None,
    chat_id: str | None = None,
    goal: str | None = None,
) -> dict[str, Any]:
    """You are dispatching Winnie, a subagent. You must pass the task in `prompt`;
    `goal` is an alias used only when `prompt` is omitted.
    Do not do the work yourself. This call blocks until Winnie finishes and returns his outcome.
    Slack is optional and posts that outcome only when a destination is set.
    """
    task = _task(prompt, goal)
    channel = _channel(chat_id)
    outcome = run_winnie(task)
    if channel is not None:
        _post_outcome(channel, outcome)
    return {
        "subagent": "winnie",
        "status": "done",
        "outcome": outcome,
        "waited": True,
    }


if __name__ == "__main__":
    mcp.run()
