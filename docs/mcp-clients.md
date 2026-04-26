# MCP Clients

Use `/settings/tokens` to create one API key per agent process. Recommended
token names are `codex`, `claude`, and `gemini`. Each token is shown once; store
the full `aq_live_...` value in Vault and configure only the matching client
with that client's token.

Suggested Vault paths:

```text
secret/agenticqueue/agent-tokens/codex
secret/agenticqueue/agent-tokens/claude
secret/agenticqueue/agent-tokens/gemini
```

The local compose stack exposes the MCP streamable HTTP endpoint at
`http://127.0.0.1:8000/mcp` and the legacy SSE endpoint at
`http://127.0.0.1:8000/mcp/sse/`. Prefer streamable HTTP unless a client only
supports SSE. The same bearer token works for the HTTP API, CLI, and MCP.

## Codex

Codex reads MCP servers from `~/.codex/config.toml`. Its current config surface
uses TOML, but this JSON shows the exact server fields to set:

```json
{
  "mcp_servers": {
    "agenticqueue": {
      "url": "http://127.0.0.1:8000/mcp",
      "bearer_token_env_var": "AGENTICQUEUE_MCP_TOKEN",
      "enabled": true,
      "tool_timeout_sec": 60
    }
  }
}
```

Load the Codex token from Vault before starting Codex:

```bash
export AGENTICQUEUE_MCP_TOKEN="$(vault kv get -field=token secret/agenticqueue/agent-tokens/codex)"
```

Add the matching TOML to `~/.codex/config.toml`:

```toml
[mcp_servers.agenticqueue]
url = "http://127.0.0.1:8000/mcp"
bearer_token_env_var = "AGENTICQUEUE_MCP_TOKEN"
enabled = true
tool_timeout_sec = 60
```

Verify the connection:

```bash
codex mcp get agenticqueue
```

## Claude Desktop

Claude Desktop local MCP configuration lives at
`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS and
`%APPDATA%\Claude\claude_desktop_config.json` on Windows. Current Claude Desktop
does not connect remote HTTP servers directly from that file; use a local
`mcp-remote` stdio bridge for the AgenticQueue HTTP endpoint.

```json
{
  "mcpServers": {
    "agenticqueue": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote",
        "http://127.0.0.1:8000/mcp",
        "--header",
        "Authorization: Bearer ${AGENTICQUEUE_MCP_TOKEN}"
      ],
      "env": {
        "AGENTICQUEUE_MCP_TOKEN": "aq_live_from_vault"
      }
    }
  }
}
```

Load the Claude token from Vault, then replace `aq_live_from_vault` in the local
JSON before restarting Claude Desktop:

```bash
export AGENTICQUEUE_MCP_TOKEN="$(vault kv get -field=token secret/agenticqueue/agent-tokens/claude)"
```

Verify the connection in Claude Desktop with `Settings -> Developer -> Edit
Config`, then restart the app and open the connectors/tool list. Expect an
`agenticqueue` server with tools such as `list_jobs` and `health_check`.

## Gemini

Gemini CLI reads MCP servers from `~/.gemini/settings.json` for user-level
configuration, or `.gemini/settings.json` for a project-level install.

```json
{
  "mcpServers": {
    "agenticqueue": {
      "httpUrl": "http://127.0.0.1:8000/mcp",
      "headers": {
        "Authorization": "Bearer aq_live_from_vault"
      },
      "timeout": 30000,
      "trust": false
    }
  }
}
```

Load the Gemini token from Vault, then render the `Authorization` header with
that value:

```bash
export AGENTICQUEUE_MCP_TOKEN="$(vault kv get -field=token secret/agenticqueue/agent-tokens/gemini)"
```

Verify the connection:

```bash
gemini mcp list
/mcp
```

## Verify your install

Set the token for the client you are testing:

```bash
export AGENTICQUEUE_MCP_TOKEN="$(vault kv get -field=token secret/agenticqueue/agent-tokens/codex)"
```

Then run this one-liner. It follows the `/mcp` slash redirect, sends bearer
auth, asks for the MCP tool list, and prints the HTTP status plus a compact
response body:

```bash
curl -fsS -L -w '\nHTTP %{http_code}\n' http://127.0.0.1:8000/mcp \
  -H "Authorization: Bearer ${AGENTICQUEUE_MCP_TOKEN}" \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

Expected result: HTTP `200` and a response containing AgenticQueue tool names
such as `list_jobs` and `health_check`. If the token is missing, expect `401`.
If the token was revoked, expect `403`.

References:

- Codex MCP config: <https://developers.openai.com/codex/config-reference>
- Claude MCP config and JSON import: <https://code.claude.com/docs/en/mcp>
- Gemini MCP config: <https://google-gemini.github.io/gemini-cli/docs/tools/mcp-server.html>
