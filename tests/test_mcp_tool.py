import asyncio
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import sys
import threading

import pytest


pytest.importorskip("mcp.client.stdio")
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


ROOT = Path(__file__).resolve().parents[1]


def test_delegate_assistant_is_available_over_stdio(tmp_path):
    requests = []
    prompt_log = tmp_path / "winnie-prompt.txt"

    class SlackHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            requests.append({"path": self.path, "body": json.loads(body)})
            response = json.dumps({"ok": True, "channel": "D1", "ts": "1.2"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, format, *args):
            pass

    http_server = HTTPServer(("127.0.0.1", 0), SlackHandler)
    server_thread = threading.Thread(target=http_server.serve_forever, daemon=True)
    server_thread.start()
    try:
        environment = os.environ.copy()
        environment.update(
            {
                "WINNIE_COMMAND": str(ROOT / "tests" / "fake_winnie.py"),
                "WINNIE_TEST_PROMPT_LOG": str(prompt_log),
                "SLACK_BOT_TOKEN": "stdio-test-token",
                "SLACK_API_URL": (
                    f"http://127.0.0.1:{http_server.server_port}/api/chat.postMessage"
                ),
            }
        )
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[str(ROOT / "server" / "slack_delegate_mcp.py")],
            env=environment,
        )

        async def exercise_server():
            async with stdio_client(parameters) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    tool = next(tool for tool in tools.tools if tool.name == "delegate_assistant")
                    description = (tool.description or "").lower()
                    assert "dispatching winnie, a subagent" in description
                    assert "task in `prompt`" in description
                    assert "blocks until winnie finishes" in description
                    assert "do not do the work yourself" in description
                    result = await session.call_tool(
                        "delegate_assistant",
                        arguments={"goal": "Ship the release", "chat_id": "D1"},
                    )
                    assert result.structuredContent == {
                        "subagent": "winnie",
                        "status": "done",
                        "outcome": "Winnie completed: Ship the release",
                        "waited": True,
                    }

        asyncio.run(asyncio.wait_for(exercise_server(), timeout=10))
        assert prompt_log.read_text() == "Ship the release"
        assert len(requests) == 1
        assert requests[0]["path"] == "/api/chat.postMessage"
        assert requests[0]["body"]["channel"] == "D1"
        assert requests[0]["body"]["text"] == "Winnie completed: Ship the release"
    finally:
        http_server.shutdown()
        http_server.server_close()
        server_thread.join(timeout=2)
