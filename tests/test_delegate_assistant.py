import asyncio
import io
import json
import os
from pathlib import Path
import sys
import threading
import urllib.error
from unittest.mock import Mock, patch

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from server.slack_delegate_mcp import DelegateError, delegate_assistant


ROOT = Path(__file__).resolve().parents[1]
TEST_TOKEN = "unit-test-token"
WINNIE_OUTCOME = "Winnie completed the investigation"


class FakeResponse:
    def __init__(self, body, status=200):
        self.body = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self):
        return self.body


@pytest.fixture(autouse=True)
def slack_environment(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", TEST_TOKEN)
    monkeypatch.setenv("SLACK_API_URL", "https://slack.invalid/api/chat.postMessage")
    monkeypatch.delenv("SLACK_DEFAULT_CHANNEL", raising=False)


@pytest.fixture(autouse=True)
def runner(monkeypatch):
    mocked = Mock(return_value=WINNIE_OUTCOME)
    monkeypatch.setattr("server.slack_delegate_mcp.run_winnie", mocked)
    return mocked


@pytest.fixture(autouse=True)
def http():
    with patch("server.slack_delegate_mcp.urllib.request.urlopen") as mocked:
        yield mocked


def request_body(http):
    request = http.call_args.args[0]
    return json.loads(request.data.decode())


def expected_result(outcome=WINNIE_OUTCOME):
    return {
        "subagent": "winnie",
        "status": "done",
        "outcome": outcome,
        "waited": True,
    }


def test_without_destination_does_not_require_token_or_call_slack(monkeypatch, runner, http):
    monkeypatch.delenv("SLACK_BOT_TOKEN")

    result = delegate_assistant(prompt="Investigate the deploy")

    assert result == expected_result()
    runner.assert_called_once_with("Investigate the deploy")
    http.assert_not_called()


def test_prompt_is_the_task_and_wins_over_goal(runner, http):
    result = delegate_assistant(prompt="Use this task", goal="Ignore this alias")

    assert result == expected_result()
    runner.assert_called_once_with("Use this task")
    http.assert_not_called()


def test_goal_is_an_alias_only_when_prompt_is_omitted(runner):
    result = delegate_assistant(goal="Use the aliased task")

    assert result == expected_result()
    runner.assert_called_once_with("Use the aliased task")


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"prompt": ""},
        {"prompt": " \n\t"},
        {"prompt": "", "goal": "Must not be used"},
        {"goal": ""},
        {"goal": " \n\t"},
        {"prompt": 42},
        {"goal": 42},
    ],
)
def test_empty_or_invalid_task_starts_nothing(arguments, runner, http):
    with pytest.raises(DelegateError, match="prompt|goal|task"):
        delegate_assistant(**arguments)

    runner.assert_not_called()
    http.assert_not_called()


@pytest.mark.parametrize("chat_id", ["", " ", "\t", "U123\n", "U123\x00", "U123\x7f", 42])
def test_invalid_chat_id_errors_before_winnie_starts(chat_id, runner, http):
    with pytest.raises(DelegateError, match="chat_id"):
        delegate_assistant(prompt="Check the deploy", chat_id=chat_id)

    runner.assert_not_called()
    http.assert_not_called()


def test_missing_token_is_checked_only_after_winnie_finishes(monkeypatch, runner, http):
    monkeypatch.delenv("SLACK_BOT_TOKEN")

    with pytest.raises(DelegateError, match="SLACK_BOT_TOKEN"):
        delegate_assistant(prompt="Check the deploy", chat_id="U123")

    runner.assert_called_once_with("Check the deploy")
    http.assert_not_called()


def test_success_runs_winnie_before_one_bearer_json_post(runner, http):
    events = []

    def finish_winnie(prompt):
        events.append(("winnie", prompt))
        return "Outcome from Winnie"

    def post_to_slack(request, timeout):
        events.append(("slack", request))
        return FakeResponse({"ok": True, "channel": "C_RETURNED", "ts": "123.456"})

    runner.side_effect = finish_winnie
    http.side_effect = post_to_slack

    result = delegate_assistant(prompt="Review the deploy", chat_id="  U_REQUESTED  ")

    assert result == expected_result("Outcome from Winnie")
    assert [event[0] for event in events] == ["winnie", "slack"]
    http.assert_called_once()
    request = http.call_args.args[0]
    assert http.call_args.kwargs["timeout"] == 15
    assert request.full_url == "https://slack.invalid/api/chat.postMessage"
    assert request.get_header("Authorization") == f"Bearer {TEST_TOKEN}"
    assert request.get_header("Content-type") == "application/json"
    body = request_body(http)
    assert body["channel"] == "U_REQUESTED"
    assert body["text"] == "Outcome from Winnie"
    assert body["unfurl_links"] is False
    assert body["unfurl_media"] is False


def test_omitted_chat_id_uses_default_channel(runner, http, monkeypatch):
    monkeypatch.setenv("SLACK_DEFAULT_CHANNEL", "C_DEFAULT")
    http.return_value = FakeResponse({"ok": True, "channel": "C_DEFAULT", "ts": "222.333"})

    result = delegate_assistant(prompt="Check the deploy")

    assert result == expected_result()
    assert request_body(http) == {
        "channel": "C_DEFAULT",
        "text": WINNIE_OUTCOME,
        "unfurl_links": False,
        "unfurl_media": False,
    }
    runner.assert_called_once_with("Check the deploy")
    http.assert_called_once()


def test_winnie_failure_prevents_slack_post(runner, http):
    runner.side_effect = DelegateError("Winnie failed safely")

    with pytest.raises(DelegateError, match="Winnie failed safely"):
        delegate_assistant(prompt="Check the deploy", chat_id="U123")

    http.assert_not_called()


def test_delegate_assistant_blocks_until_winnie_returns(runner, http):
    runner_started = threading.Event()
    allow_runner_return = threading.Event()
    call_finished = threading.Event()
    results = []

    def blocked_runner(prompt):
        runner_started.set()
        assert allow_runner_return.wait(timeout=2)
        return "Released outcome"

    def call_tool():
        results.append(delegate_assistant(prompt="Wait for Winnie"))
        call_finished.set()

    runner.side_effect = blocked_runner
    caller = threading.Thread(target=call_tool)
    caller.start()
    try:
        assert runner_started.wait(timeout=2)
        assert not call_finished.is_set()
        allow_runner_return.set()
        assert call_finished.wait(timeout=2)
    finally:
        allow_runner_return.set()
        caller.join(timeout=2)

    assert results == [expected_result("Released outcome")]
    http.assert_not_called()


def test_slack_error_contains_code_after_exactly_one_post(runner, http):
    http.return_value = FakeResponse({"ok": False, "error": "channel_not_found"})

    with pytest.raises(DelegateError, match="channel_not_found") as caught:
        delegate_assistant(prompt="Check the deploy", chat_id="C_MISSING")

    runner.assert_called_once()
    http.assert_called_once()
    assert TEST_TOKEN not in str(caught.value)


def test_http_error_contains_slack_code_without_headers_or_token(runner, http):
    response = io.BytesIO(json.dumps({"ok": False, "error": "ratelimited"}).encode())
    http.side_effect = urllib.error.HTTPError(
        "https://slack.invalid/api/chat.postMessage",
        429,
        "too many requests",
        None,
        response,
    )

    with pytest.raises(DelegateError, match="ratelimited") as caught:
        delegate_assistant(prompt="Check the deploy", chat_id="U123")

    runner.assert_called_once()
    http.assert_called_once()
    assert TEST_TOKEN not in str(caught.value)
    assert "headers" not in str(caught.value).lower()


def test_non_json_response_is_a_sanitized_tool_error(runner, http):
    http.return_value = FakeResponse(b"not JSON; unit-test-token must not leak")

    with pytest.raises(DelegateError, match="JSON") as caught:
        delegate_assistant(prompt="Check the deploy", chat_id="U123")

    runner.assert_called_once()
    http.assert_called_once()
    assert TEST_TOKEN not in str(caught.value)
    assert "not JSON" not in str(caught.value)


def test_mcp_stdio_description_and_call_use_local_winnie_command(tmp_path):
    call_log = tmp_path / "stdio-slack-calls.json"
    prompt_log = tmp_path / "stdio-winnie-prompt.txt"

    async def exercise_client():
        environment = {
            **os.environ,
            "WINNIE_COMMAND": str(ROOT / "tests" / "fake_winnie.py"),
            "WINNIE_TEST_PROMPT_LOG": str(prompt_log),
            "SLACK_TEST_CALL_LOG": str(call_log),
        }
        environment.pop("SLACK_BOT_TOKEN", None)
        environment.pop("SLACK_DEFAULT_CHANNEL", None)
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[str(ROOT / "tests" / "stdio_mock_server.py")],
            cwd=str(ROOT),
            env=environment,
        )
        async with stdio_client(parameters) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert [tool.name for tool in tools.tools] == ["delegate_assistant"]
                description = (tools.tools[0].description or "").lower()
                assert "winnie" in description
                assert "subagent" in description
                assert "task in `prompt`" in description
                assert "blocks until winnie finishes" in description
                assert "do not do the work yourself" in description

                result = await session.call_tool(
                    "delegate_assistant",
                    {"prompt": "Work through stdio"},
                )
                assert result.isError is False
                assert result.structuredContent == expected_result(
                    "Winnie completed: Work through stdio"
                )

    asyncio.run(asyncio.wait_for(exercise_client(), timeout=15))

    assert prompt_log.read_text() == "Work through stdio"
    assert not call_log.exists()
