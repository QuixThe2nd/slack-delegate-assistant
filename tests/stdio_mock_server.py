"""Run the real server entrypoint with urllib intercepted in this subprocess."""

import json
import os
from pathlib import Path
import runpy
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self):
        return json.dumps({"ok": True, "channel": "C_STDIO", "ts": "567.890"}).encode()


def fake_urlopen(request, timeout):
    log_path = Path(os.environ["SLACK_TEST_CALL_LOG"])
    calls = json.loads(log_path.read_text()) if log_path.exists() else []
    calls.append({"body": json.loads(request.data.decode()), "timeout": timeout})
    log_path.write_text(json.dumps(calls))
    return FakeResponse()


with patch("urllib.request.urlopen", fake_urlopen):
    runpy.run_path(str(ROOT / "server" / "slack_delegate_mcp.py"), run_name="__main__")
