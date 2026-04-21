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

## Operations

Legend:

- Caps = capability required on the calling actor
- Ticket = owning AgenticQueue ticket
- `✦` = mutation
- `○` = read-only

### 1. Actor / Identity

| # | Op | CLI | REST | MCP tool | Caps | Ticket |
|---|---|---|---|---|---|---|
| 1.1 ○ | whoami | `aq whoami` | `GET /actors/me` | `get_self` | — | AQ-43 ✓ |
| 1.2 ✦ | create actor | `aq actor create --name X --caps a,b` | `POST /actors` | `create_actor` | `admin` | AQ-43 ✓ |
| 1.3 ○ | list actors | `aq actor list` | `GET /actors` | `list_actors` | `admin` or self-scope | AQ-43 ✓ |
| 1.4 ✦ | revoke actor | `aq actor revoke <id>` | `POST /actors/{id}/revoke` | `revoke_actor` | `admin` | AQ-43 ✓ |
| 1.5 ✦ | grant capability | `aq actor grant <id> --caps x` | `POST /actors/{id}/caps` | `grant_capability` | `admin` | AQ-45 ✓ |
| 1.6 ✦ | revoke capability | `aq actor revoke-cap <id> --caps x` | `DELETE /actors/{id}/caps` | `revoke_capability` | `admin` | AQ-45 ✓ |
| 1.7 ✦ | rotate own key | `aq key rotate` | `POST /actors/me/rotate-key` | `rotate_own_key` | self | AQ-43 |

### 2. Project

| # | Op | CLI | REST | MCP tool | Caps | Ticket |
|---|---|---|---|---|---|---|
| 2.1 ✦ | create project | `aq project create --name X` | `POST /projects` | `create_project` | `admin` | AQ-48 ✓ |
| 2.2 ○ | list projects | `aq project list` | `GET /projects` | `list_projects` | — | AQ-48 ✓ |
| 2.3 ○ | get project | `aq project get <id>` | `GET /projects/{id}` | `get_project` | — | AQ-48 ✓ |
| 2.4 ✦ | update project | `aq project update <id> --surface-areas ...` | `PATCH /projects/{id}` | `update_project` | `admin` | AQ-48 ✓ |
| 2.5 ✦ | archive project | `aq project archive <id>` | `POST /projects/{id}/archive` | `archive_project` | `admin` | AQ-48 ✓ |

### 3. Pipeline

| # | Op | CLI | REST | MCP tool | Caps | Ticket |
|---|---|---|---|---|---|---|
| 3.1 ✦ | create pipeline | `aq pipeline create --project X --title Y` | `POST /pipelines` | `create_pipeline` | `write:pipeline` | AQ-48 ✓ |
| 3.2 ○ | list pipelines | `aq pipeline list [--state ...]` | `GET /pipelines` | `list_pipelines` | `read` | AQ-48 ✓ |
| 3.3 ○ | get pipeline | `aq pipeline get <id>` | `GET /pipelines/{id}` | `get_pipeline` | `read` | AQ-48 ✓ |
| 3.4 ✦ | update pipeline | `aq pipeline update <id> ...` | `PATCH /pipelines/{id}` | `update_pipeline` | `write:pipeline` | AQ-48 ✓ |
| 3.5 ✦ | cancel pipeline | `aq pipeline cancel <id>` | `POST /pipelines/{id}/cancel` | `cancel_pipeline` | `write:pipeline` | AQ-48 ✓ |

### 4. Job

| # | Op | CLI | REST | MCP tool | Caps | Ticket |
|---|---|---|---|---|---|---|
| 4.1 ✦ | create job | `aq job create --pipeline X --type coding-task --contract ...` | `POST /jobs` | `create_job` | `write:job` | AQ-48 ✓ |
| 4.2 ○ | list jobs | `aq job list [--state ...]` | `GET /jobs` | `list_jobs` | `read` | AQ-48 ✓ |
| 4.3 ○ | get job | `aq job get <id>` | `GET /jobs/{id}` | `get_job` | `read` | AQ-48 ✓ |
| 4.4 ✦ | update job | `aq job update <id> --title ...` | `PATCH /jobs/{id}` | `update_job` | `write:job` | AQ-48 ✓ |
| 4.5 ✦ | claim next job | `aq claim [--project X]` | `POST /jobs/claim` | `claim_next_job` | capability-matched | AQ-96 |
| 4.6 ✦ | release claim | `aq release <id>` | `POST /jobs/{id}/release` | `release_job` | claimant | AQ-96 |
| 4.7 ✦ | submit payload | `aq submit <id> --payload ...` | `POST /jobs/{id}/submit` | `submit_payload` | claimant | AQ-96 |
| 4.8 ✦ | approve (HITL) | `aq approve <id>` | `POST /jobs/{id}/approve` | `approve_job` | `approve` | AQ-98 |
| 4.9 ✦ | reject (HITL) | `aq reject <id> --reason ...` | `POST /jobs/{id}/reject` | `reject_job` | `approve` | AQ-98 |
| 4.10 ✦ | force-unlock escrow | `aq escrow unlock <id> --reason ...` | `POST /jobs/{id}/escrow-unlock` | `force_unlock_escrow` | `supervisor` | AQ-96 |
| 4.11 ✦ | reset DLQ job | `aq job reset <id>` | `POST /jobs/{id}/reset` | `reset_job` | `admin` | AQ-181 |
| 4.12 ✦ | comment on job | `aq job comment <id> --body ...` | `POST /jobs/{id}/comments` | `comment_on_job` | `read` | AQ-48 ✓ |

### 5. Contract / task-type

| # | Op | CLI | REST | MCP tool | Caps | Ticket |
|---|---|---|---|---|---|---|
| 5.1 ✦ | register task-type | `aq task-type register path/to/x.json` | `POST /task-types` | `register_task_type` | `admin` | AQ-51 ✓ |
| 5.2 ○ | list task-types | `aq task-type list` | `GET /task-types` | `list_task_types` | `read` | AQ-51 ✓ |
| 5.3 ○ | get task-type | `aq task-type get <name>` | `GET /task-types/{name}` | `get_task_type` | `read` | AQ-51 ✓ |
| 5.4 ✦ | update task-type | `aq task-type update <name> --schema ...` | `PATCH /task-types/{name}` | `update_task_type` | `admin` | AQ-51 ✓ |

### 6. Decision

| # | Op | CLI | REST | MCP tool | Caps | Ticket |
|---|---|---|---|---|---|---|
| 6.1 ✦ | create decision | `aq decision create --title X --body ...` | `POST /decisions` | `create_decision` | `write:decision` | AQ-48 ✓ |
| 6.2 ○ | list decisions | `aq decision list` | `GET /decisions` | `list_decisions` | `read` | AQ-48 ✓ |
| 6.3 ○ | get decision | `aq decision get <id>` | `GET /decisions/{id}` | `get_decision` | `read` | AQ-48 ✓ |
| 6.4 ✦ | supersede decision | `aq decision supersede <old> --with <new>` | `POST /decisions/{id}/supersede` | `supersede_decision` | `write:decision` | AQ-40 ✓ |
| 6.5 ✦ | link decision to job | `aq decision link <id> --job X` | `POST /decisions/{id}/link` | `link_decision` | `write:decision` | AQ-40 ✓ |

### 7. Learning

| # | Op | CLI | REST | MCP tool | Caps | Ticket |
|---|---|---|---|---|---|---|
| 7.1 ✦ | submit learning | `aq learning submit ...` | `POST /learnings` | `submit_learning` | `write:learning` | AQ-60 ✓ |
| 7.2 ○ | list learnings | `aq learning list [--scope ...]` | `GET /learnings` | `list_learnings` | `read` | AQ-68 ✓ |
| 7.3 ○ | get learning | `aq learning get <id>` | `GET /learnings/{id}` | `get_learning` | `read` | AQ-68 ✓ |
| 7.4 ○ | search learnings | `aq learning search \"...\"` | `GET /learnings/search?q=...` | `search_learnings` | `read` | AQ-68 ✓ + AQ-80 |
| 7.5 ✦ | promote learning | `aq learning promote <id> --to project` | `POST /learnings/{id}/promote` | `promote_learning` | `write:learning` | AQ-66 ✓ |
| 7.6 ✦ | supersede learning | `aq learning supersede <old> --with <new>` | `POST /learnings/{id}/supersede` | `supersede_learning` | `write:learning` | AQ-65 ✓ |
| 7.7 ✦ | expire learning | `aq learning expire <id>` | `POST /learnings/{id}/expire` | `expire_learning` | `write:learning` | AQ-65 ✓ |
| 7.8 ✦ | edit learning | `aq learning edit <id> --from ...` | `PATCH /learnings/{id}` | `edit_learning` | `write:learning` | AQ-63 ✓ |

### 8. Graph / Context

| # | Op | CLI | REST | MCP tool | Caps | Ticket |
|---|---|---|---|---|---|---|
| 8.1 ○ | neighborhood | `aq graph neighborhood <id> --hops N` | `GET /graph/neighborhood/{id}?hops=N` | `query_graph` | `read` | AQ-41 ✓ + AQ-71 ✓ |
| 8.2 ○ | traverse | `aq graph traverse <id> --edge X` | `GET /graph/traverse/{id}?edge=X` | `traverse_graph` | `read` | AQ-41 ✓ |
| 8.3 ○ | surface search | `aq surface search --tag X` | `GET /graph/surface?tag=X` | `search_surface` | `read` | AQ-82 + AQ-86 |
| 8.4 ○ | compile packet | `aq packet <job_id>` | `GET /jobs/{id}/packet` | `compile_packet` | `read` | AQ-75 ✓ + AQ-76 ✓ + AQ-77 ✓ |

### 9. Policy pack

| # | Op | CLI | REST | MCP tool | Caps | Ticket |
|---|---|---|---|---|---|---|
| 9.1 ✦ | load pack | `aq policy load path/to/x.yaml` | `POST /policies` | `load_policy_pack` | `admin` | AQ-57 ✓ |
| 9.2 ○ | list packs | `aq policy list` | `GET /policies` | `list_policy_packs` | `read` | AQ-57 ✓ |
| 9.3 ○ | get pack | `aq policy get <name>` | `GET /policies/{name}` | `get_policy_pack` | `read` | AQ-57 ✓ |
| 9.4 ✦ | attach pack | `aq policy attach --pipeline X --pack Y` | `POST /pipelines/{id}/policy` | `attach_policy` | `write:pipeline` | AQ-57 ✓ |

### 10. Audit / Runs

| # | Op | CLI | REST | MCP tool | Caps | Ticket |
|---|---|---|---|---|---|---|
| 10.1 ○ | list runs | `aq run list [--job ...]` | `GET /runs` | `list_runs` | `read` | AQ-48 ✓ |
| 10.2 ○ | get run | `aq run get <id>` | `GET /runs/{id}` | `get_run` | `read` | AQ-48 ✓ |
| 10.3 ○ | query audit | `aq audit [--actor ...] [--since ...]` | `GET /audit` | `query_audit_log` | `admin` | AQ-126 |

### 11. Artifacts

| # | Op | CLI | REST | MCP tool | Caps | Ticket |
|---|---|---|---|---|---|---|
| 11.1 ✦ | attach artifact | `aq artifact attach <job_id> --kind pr --url ...` | `POST /artifacts` | `attach_artifact` | `write:job` | AQ-48 ✓ |
| 11.2 ○ | list artifacts | `aq artifact list --job X` | `GET /artifacts?job=X` | `list_artifacts` | `read` | AQ-48 ✓ |
| 11.3 ○ | get artifact | `aq artifact get <id>` | `GET /artifacts/{id}` | `get_artifact` | `read` | AQ-48 ✓ |

### 12. Admin / System

| # | Op | CLI | REST | MCP tool | Caps | Ticket |
|---|---|---|---|---|---|---|
| 12.1 ○ | health | `aq health` | `GET /healthz` | `health_check` | — | AQ-138 |
| 12.2 ○ | stats | `aq stats` | `GET /stats` | `get_stats` | `read` | AQ-138 |
| 12.3 ✦ | first-run setup | `aq setup` | `POST /setup` (disabled after first run) | n/a | initial | AQ-137 |

## Coverage audit

The table maps 48 canonical operations across the public surfaces. Small scope
additions were still required when the table was first assembled, but no new
transport families were introduced beyond CLI, REST, and MCP.

## Counts

- Total operations: 48
- Mutations: 27
- Reads: 21
- Capability gates: `admin`, `supervisor`, `read`, `approve`, `write:*`
