# MCP Client Tokens

Use `/settings/tokens` to create separate API keys for each MCP client and
agent process. Recommended names are `codex`, `claude`, and `gemini`.

Store each `aq_live_...` token in Vault before configuring the client:

```text
secret/agenticqueue/agent-tokens/codex
secret/agenticqueue/agent-tokens/claude
secret/agenticqueue/agent-tokens/gemini
```

Configure the API, CLI, or MCP server with that agent's token:

```text
AQ_TOKEN=aq_live_...
```
