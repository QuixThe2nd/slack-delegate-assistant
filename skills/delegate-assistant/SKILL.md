---
name: delegate-assistant
description: "Use when a task should be handed to Winnie. Call delegate_assistant with the task in prompt and wait until he finishes."
---

Call `delegate_assistant` with the full task in `prompt`. You are dispatching Winnie, a subagent. The call blocks until Winnie finishes. Do not do the work yourself. Use his `outcome` as the result.
