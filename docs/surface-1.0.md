# AgenticQueue 1.0 Canonical Transport Surface

This document is the public, bundled copy of the AgenticQueue v1.0 transport
surface. The API, CLI, and MCP layers derive their parity checks from this file
so the public repository remains self-contained in local and CI environments.

## Transport parity

Every row below has three equivalent forms with matching semantics and payloads:

- CLI: `aq <command>`
- REST: HTTP + JSON
- MCP: FastMCP tool

The CLI stays a thin wrapper over REST. MCP tool names are the canonical
snake_case identifiers.

The REST transport preserves the persisted resource names used by the data
model:

- CLI `project` commands map to `/v1/workspaces...`
- CLI `pipeline` commands map to `/v1/projects...`
- CLI `job` commands map to `/v1/tasks...`
- `aq whoami` reads `/v1/auth/tokens` and emits the `actor` subpayload

## Unified MCP listing

The unified `AgenticQueue` MCP server only exposes the canonical public surface
listed in this document.

- Canonical MCP count: 62 tools.
- First-run bootstrap is intentionally REST/UI-only and does not have a unified
  MCP tool.
- The standalone learnings and memory helpers
  (`get_relevant_learnings`, `submit_task_learning`, `search_memory`,
  `sync_memory`, `memory_stats`) are not mounted into the unified server
  because they are adapter-specific helpers, not part of the 1.0 public
  transport contract.

## MCP discoverability profiles

The unified MCP `tools/list` response is profile-filtered. Discoverability is
separate from authorization: visible tools still enforce the normal
capability/token checks at execution time.

- `worker`: 30 tools, default public listing
- `reviewer`: 38 tools
- `supervisor`: 45 tools
- `admin`: 62 tools

Workers do not see actor/capability mutation, task-type mutation, policy
mutation, audit query, or first-run bootstrap operations in the unified MCP
catalog.

## Operations

Legend:

- Caps = capability required on the calling actor
- Ticket = owning AgenticQueue ticket
- `✦` = mutation
- `○` = read-only

### 1. Actor / Identity

| # | Op | CLI | REST | MCP tool | Caps | Ticket |
|---|---|---|---|---|---|---|
| 1.1 ○ | whoami | `aq whoami` | `GET /v1/auth/tokens` | `get_self` | — | AQ-43 ✓ |
| 1.2 ✦ | create actor | `aq actor create --name X --caps a,b` | `POST /v1/actors` | `create_actor` | `admin` | AQ-43 ✓ |
| 1.3 ○ | list actors | `aq actor list` | `GET /v1/actors` | `list_actors` | `admin` or self-scope | AQ-43 ✓ |
| 1.4 ✦ | revoke actor | `aq actor revoke <id>` | `DELETE /v1/actors/{id}` | `revoke_actor` | `admin` | AQ-43 ✓ |
| 1.5 ✦ | grant capability | `aq actor grant <id> --caps x` | `POST /v1/capabilities/grant` | `grant_capability` | `admin` | AQ-45 ✓ |
| 1.6 ✦ | revoke capability | `aq actor revoke-cap <id> --caps x` | `POST /v1/capabilities/revoke` | `revoke_capability` | `admin` | AQ-45 ✓ |
| 1.7 ✦ | rotate own key | `aq key rotate` | `POST /v1/actors/me/rotate-key` | `rotate_own_key` | self | AQ-43 |

### 2. Project

| # | Op | CLI | REST | MCP tool | Caps | Ticket |
|---|---|---|---|---|---|---|
| 2.1 ✦ | create project | `aq project create --name X` | `POST /v1/workspaces` | `create_project` | `admin` | AQ-48 ✓ |
| 2.2 ○ | list projects | `aq project list` | `GET /v1/workspaces` | `list_projects` | — | AQ-48 ✓ |
| 2.3 ○ | get project | `aq project get <id>` | `GET /v1/workspaces/{id}` | `get_project` | — | AQ-48 ✓ |
| 2.4 ✦ | update project | `aq project update <id> --surface-areas ...` | `PATCH /v1/workspaces/{id}` | `update_project` | `admin` | AQ-48 ✓ |
| 2.5 ✦ | archive project | `aq project archive <id>` | `DELETE /v1/workspaces/{id}` | `archive_project` | `admin` | AQ-48 ✓ |

### 3. Pipeline

| # | Op | CLI | REST | MCP tool | Caps | Ticket |
|---|---|---|---|---|---|---|
| 3.1 ✦ | create pipeline | `aq pipeline create --project X --title Y` | `POST /v1/projects` | `create_pipeline` | `write:pipeline` | AQ-48 ✓ |
| 3.2 ○ | list pipelines | `aq pipeline list [--state ...]` | `GET /v1/projects` | `list_pipelines` | `read` | AQ-48 ✓ |
| 3.3 ○ | get pipeline | `aq pipeline get <id>` | `GET /v1/projects/{id}` | `get_pipeline` | `read` | AQ-48 ✓ |
| 3.4 ✦ | update pipeline | `aq pipeline update <id> ...` | `PATCH /v1/projects/{id}` | `update_pipeline` | `write:pipeline` | AQ-48 ✓ |
| 3.5 ✦ | cancel pipeline | `aq pipeline cancel <id>` | `DELETE /v1/projects/{id}` | `cancel_pipeline` | `write:pipeline` | AQ-48 ✓ |

### 4. Job

| # | Op | CLI | REST | MCP tool | Caps | Ticket |
|---|---|---|---|---|---|---|
| 4.1 ✦ | create job | `aq job create --pipeline X --type coding-task --contract ...` | `POST /v1/tasks` | `create_job` | `write:job` | AQ-48 ✓ |
| 4.2 ○ | list jobs | `aq job list [--state ...]` | `GET /v1/tasks` | `list_jobs` | `read` | AQ-48 ✓ |
| 4.3 ○ | get job | `aq job get <id>` | `GET /v1/tasks/{id}` | `get_job` | `read` | AQ-48 ✓ |
| 4.4 ✦ | update job | `aq job update <id> --title ...` | `PATCH /v1/tasks/{id}` | `update_job` | `write:job` | AQ-48 ✓ |
| 4.5 ✦ | claim next job | `aq claim [--project X]` | `POST /v1/tasks/claim` | `claim_next_job` | capability-matched | AQ-96 |
| 4.6 ✦ | release claim | `aq release <id>` | `POST /v1/tasks/{id}/release` | `release_job` | claimant | AQ-96 |
| 4.7 ✦ | submit payload | `aq submit <id> --payload ...` | `POST /v1/tasks/{id}/submit` | `submit_payload` | claimant | AQ-96 |
| 4.8 ✦ | approve (HITL) | `aq approve <id>` | `POST /v1/tasks/{id}/approve` | `approve_job` | `approve` | AQ-98 |
| 4.9 ✦ | reject (HITL) | `aq reject <id> --reason ...` | `POST /v1/tasks/{id}/reject` | `reject_job` | `approve` | AQ-98 |
| 4.10 ✦ | force-unlock escrow | `aq escrow unlock <id> --reason ...` | `POST /v1/tasks/{id}/escrow-unlock` | `force_unlock_escrow` | `supervisor` | AQ-96 |
| 4.11 ✦ | reset DLQ job | `aq job reset <id>` | `POST /v1/tasks/{id}/reset` | `reset_job` | `admin` | AQ-181 |
| 4.12 ✦ | comment on job | `aq job comment <id> --body ...` | `POST /v1/tasks/{id}/comments` | `comment_on_job` | `read` | AQ-48 ✓ |

### 5. Contract / task-type

| # | Op | CLI | REST | MCP tool | Caps | Ticket |
|---|---|---|---|---|---|---|
| 5.1 ✦ | register task-type | `aq task-type register path/to/x.json` | `POST /v1/task-types` | `register_task_type` | `admin` | AQ-51 ✓ |
| 5.2 ○ | list task-types | `aq task-type list` | `GET /v1/task-types` | `list_task_types` | `read` | AQ-51 ✓ |
| 5.3 ○ | get task-type | `aq task-type get <name>` | `GET /v1/task-types/{name}` | `get_task_type` | `read` | AQ-51 ✓ |
| 5.4 ✦ | update task-type | `aq task-type update <name> --schema ...` | `PATCH /v1/task-types/{name}` | `update_task_type` | `admin` | AQ-51 ✓ |

### 6. Decision

| # | Op | CLI | REST | MCP tool | Caps | Ticket |
|---|---|---|---|---|---|---|
| 6.1 ✦ | create decision | `aq decision create --title X --body ...` | `POST /v1/decisions` | `create_decision` | `write:decision` | AQ-48 ✓ |
| 6.2 ○ | list decisions | `aq decision list` | `GET /v1/decisions` | `list_decisions` | `read` | AQ-48 ✓ |
| 6.3 ○ | get decision | `aq decision get <id>` | `GET /v1/decisions/{id}` | `get_decision` | `read` | AQ-48 ✓ |
| 6.4 ✦ | supersede decision | `aq decision supersede <old> --with <new>` | `POST /v1/decisions/{id}/supersede` | `supersede_decision` | `write:decision` | AQ-40 ✓ |
| 6.5 ✦ | link decision to job | `aq decision link <id> --job X` | `POST /v1/decisions/{id}/link` | `link_decision` | `write:decision` | AQ-40 ✓ |

### 7. Learning

| # | Op | CLI | REST | MCP tool | Caps | Ticket |
|---|---|---|---|---|---|---|
| 7.1 ✦ | submit learning | `aq learning submit ...` | `POST /v1/learnings/submit` | `submit_learning` | `write:learning` | AQ-60 ✓ |
| 7.2 ○ | list learnings | `aq learning list [--scope ...]` | `GET /v1/learnings` | `list_learnings` | `read` | AQ-68 ✓ |
| 7.3 ○ | get learning | `aq learning get <id>` | `GET /v1/learnings/{id}` | `get_learning` | `read` | AQ-68 ✓ |
| 7.4 ○ | search learnings | `aq learning search \"...\"` | `GET /v1/learnings/search?q=...` | `search_learnings` | `read` | AQ-68 ✓ + AQ-80 |
| 7.5 ✦ | promote learning | `aq learning promote <id> --to project` | `POST /v1/learnings/{id}/promote` | `promote_learning` | `write:learning` | AQ-66 ✓ |
| 7.6 ✦ | supersede learning | `aq learning supersede <old> --with <new>` | `POST /v1/learnings/{id}/supersede` | `supersede_learning` | `write:learning` | AQ-65 ✓ |
| 7.7 ✦ | expire learning | `aq learning expire <id>` | `PATCH /v1/learnings/{id}` | `expire_learning` | `write:learning` | AQ-65 ✓ |
| 7.8 ✦ | edit learning | `aq learning edit <id> --from ...` | `PATCH /v1/learnings/{id}` | `edit_learning` | `write:learning` | AQ-63 ✓ |

### 8. Graph / Context

| # | Op | CLI | REST | MCP tool | Caps | Ticket |
|---|---|---|---|---|---|---|
| 8.1 ○ | neighborhood | `aq graph neighborhood <id> --hops N` | `GET /v1/graph/neighborhood/{id}?hops=N` | `query_graph` | `read` | AQ-41 ✓ + AQ-71 ✓ |
| 8.2 ○ | traverse | `aq graph traverse <id> --edge X` | `GET /v1/graph/traverse/{id}?edge=X` | `traverse_graph` | `read` | AQ-41 ✓ |
| 8.3 ○ | surface search | `aq surface search --tag X` | `GET /v1/graph/surface?tag=X` | `search_surface` | `read` | AQ-82 + AQ-86 |
| 8.4 ○ | compile packet | `aq packet <job_id>` | `GET /v1/tasks/{id}/packet` | `compile_packet` | `read` | AQ-75 ✓ + AQ-76 ✓ + AQ-77 ✓ |

### 9. Policy pack

| # | Op | CLI | REST | MCP tool | Caps | Ticket |
|---|---|---|---|---|---|---|
| 9.1 ✦ | load pack | `aq policy load path/to/x.yaml` | `POST /v1/policies` | `load_policy_pack` | `admin` | AQ-57 ✓ |
| 9.2 ○ | list packs | `aq policy list` | `GET /v1/policies` | `list_policy_packs` | `read` | AQ-57 ✓ |
| 9.3 ○ | get pack | `aq policy get <name>` | `GET /v1/policies/{id}` | `get_policy_pack` | `read` | AQ-57 ✓ |
| 9.4 ✦ | attach pack | `aq policy attach --pipeline X --pack Y` | `PATCH /v1/projects/{id}` | `attach_policy` | `write:pipeline` | AQ-57 ✓ |

### 10. Audit / Runs

| # | Op | CLI | REST | MCP tool | Caps | Ticket |
|---|---|---|---|---|---|---|
| 10.1 ○ | list runs | `aq run list [--job ...]` | `GET /v1/runs` | `list_runs` | `read` | AQ-48 ✓ |
| 10.2 ○ | get run | `aq run get <id>` | `GET /v1/runs/{id}` | `get_run` | `read` | AQ-48 ✓ |
| 10.3 ○ | query audit | `aq audit [--actor ...] [--since ...]` | `GET /v1/audit` | `query_audit_log` | `admin` | AQ-126 |

### 11. Artifacts

| # | Op | CLI | REST | MCP tool | Caps | Ticket |
|---|---|---|---|---|---|---|
| 11.1 ✦ | attach artifact | `aq artifact attach <job_id> --kind pr --url ...` | `POST /v1/artifacts` | `attach_artifact` | `write:job` | AQ-48 ✓ |
| 11.2 ○ | list artifacts | `aq artifact list --job X` | `GET /v1/artifacts?task_id=X` | `list_artifacts` | `read` | AQ-48 ✓ |
| 11.3 ○ | get artifact | `aq artifact get <id>` | `GET /v1/artifacts/{id}` | `get_artifact` | `read` | AQ-48 ✓ |

### 12. Admin / System

| # | Op | CLI | REST | MCP tool | Caps | Ticket |
|---|---|---|---|---|---|---|
| 12.1 ○ | health | `aq health` | `GET /healthz` | `health_check` | — | AQ-138 |
| 12.2 ○ | stats | `aq stats` | `GET /stats` | `get_stats` | `read` | AQ-138 |
| 12.3 ○ | bootstrap status | n/a | `GET /api/auth/bootstrap_status` | n/a | initial | AQ-293 |

## Coverage audit

The table maps 63 canonical transport operations across the public surfaces.
There are 62 canonical MCP tools because first-run bootstrap remains
intentionally REST/UI-only. No transport families were introduced beyond CLI,
REST, and MCP.

## Counts

- Total operations: 63
- Mutations: 34
- Reads: 29
- Canonical MCP tools: 62
- Unified MCP profile counts: `worker` 30, `reviewer` 38, `supervisor` 45, `admin` 62
- Capability gates: `admin`, `supervisor`, `approve`, `read`, `write:decision`, `write:job`, `write:learning`, `write:pipeline`, `capability-matched`, `claimant`, `self`, `initial`
