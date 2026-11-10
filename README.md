# Slack Delegate Assistant

`delegate_assistant` dispatches Winnie, a subagent. Pass the task in `prompt`. The call blocks until Winnie finishes and returns his outcome. Do not do the work yourself.

Winnie uses the persona in `prompts/winnie.md`, the same assistant role as the existing Winnie profile: male, he/him, concise, and working for Parsa and Big Steve. Claude runs him with `claude -p`. Codex runs him with `codex exec`. `goal` is accepted only as an alias when `prompt` is omitted.

Slack is optional. If `chat_id` or `SLACK_DEFAULT_CHANNEL` is set, the outcome is posted once after Winnie finishes. With no destination, no Slack token is required.

## Slack setup

The bot token requires the `chat:write` scope.

- `SLACK_BOT_TOKEN` (optional): required only when posting an outcome. If unset, `bin/run-server` reads `~/.hermes/secrets/slack-delegate.env`.
- `SLACK_DEFAULT_CHANNEL` (optional): recipient used only when `chat_id` is omitted.
- `SLACK_API_URL` (optional): defaults to `https://slack.com/api/chat.postMessage`.

## Claude Code

From this repository:

```sh
claude --plugin-dir .
```

Claude starts `sh ${CLAUDE_PLUGIN_ROOT}/bin/run-server`.

## Codex

This repository is the plugin root. The installed Codex CLI accepts:

```sh
codex plugin marketplace add /absolute/path/to/slack-delegate-assistant
codex plugin add slack-delegate-assistant@slack-delegate-local
```

Codex starts `sh ${PLUGIN_ROOT}/bin/run-server`. Restart Codex after installing.

## Tests

```sh
uv venv --seed .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

Tests use mocks and a local HTTP server. No live message was sent.
