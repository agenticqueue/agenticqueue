# Agent API Keys

Create one API key per agent in `Settings -> API keys`.

Recommended names:

- `codex`
- `claude`
- `gemini`

Each key is shown once. Store the full `aq_live_...` value in Vault, then give
the agent only the key for its own identity.

Suggested Vault paths:

```text
secret/agenticqueue/agent-tokens/codex
secret/agenticqueue/agent-tokens/claude
secret/agenticqueue/agent-tokens/gemini
```

Use the stored token as bearer auth for the API, CLI, and MCP server:

```text
Authorization: Bearer aq_live_...
AQ_TOKEN=aq_live_...
```
