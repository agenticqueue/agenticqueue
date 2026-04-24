from __future__ import annotations

from pathlib import Path
from typing import Any
import uuid

import yaml  # type: ignore[import-untyped]
from fastapi import FastAPI, Request

from agenticqueue_api.middleware.secret_redaction import SecretRedactionMiddleware


def fake_aws_access_key() -> str:
    return "AKIA" + "1234567890ABCDEF"


def fake_aws_secret_access_key() -> str:
    return "wJalrXUtnFEMI/K7MDENG/bPxRfiC" + "YEXAMPLEKEY"


def fake_github_pat() -> str:
    return "gh" + "p_" + "1234567890abcdef1234567890abcdef1234"


def fake_stripe_live_secret() -> str:
    return "sk" + "_live_" + "1234567890abcdefghijklmnop"


def fake_slack_bot_token() -> str:
    return "xox" + "b-" + "123456789012-abcdefabcdefabcd"


def policy_dir(tmp_path: Path, *, hard_block_secrets: bool) -> Path:
    directory = tmp_path / "policies"
    directory.mkdir()
    (directory / "default-coding.policy.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "1.0.0",
                "hitl_required": True,
                "autonomy_tier": 3,
                "capabilities": ["read_repo", "write_branch"],
                "body": {"hard_block_secrets": hard_block_secrets},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return directory


def build_app(tmp_path: Path, *, hard_block_secrets: bool) -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        SecretRedactionMiddleware,
        policy_directory=policy_dir(
            tmp_path,
            hard_block_secrets=hard_block_secrets,
        ),
    )

    @app.post("/v1/tasks")
    async def create_task(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "payload": payload,
            "redaction": getattr(request.state, "secret_redaction_context", None),
        }

    @app.get("/healthz")
    async def healthz() -> dict[str, bool]:
        return {"ok": True}

    return app


def secret_corpus() -> list[tuple[str, dict[str, Any]]]:
    secrets = {
        "aws_access_key": fake_aws_access_key(),
        "aws_secret_access_key": fake_aws_secret_access_key(),
        "github_pat": fake_github_pat(),
        "gcp_service_account": '{"type":"service_account","private_key_id":"abc123"}',
        "ssh_private_key": "-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----",
        "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTYifQ.signaturepart",
        "stripe_live_secret": fake_stripe_live_secret(),
        "slack_bot_token": fake_slack_bot_token(),
        "bearer_token_url": "https://example.com/hook?access_token=Bearer%20abcdEFGH1234",
        "generic_high_entropy": "Q29kZXhTZWNyZXQtVG9rZW4tQUJDREVGR0hJSktMTU5PUFFSU1RVVldY",
    }
    wrappers = [
        lambda secret: {"description": secret},
        lambda secret: {"description": f"prefix {secret} suffix"},
        lambda secret: {"nested": {"note": secret}},
        lambda secret: {"items": [secret]},
        lambda secret: {"items": [{"note": secret}]},
    ]

    corpus: list[tuple[str, dict[str, Any]]] = []
    for kind, secret in secrets.items():
        kind_wrappers = wrappers
        if kind == "generic_high_entropy":
            kind_wrappers = [lambda secret: {"description": secret}] * len(wrappers)
        for wrap in kind_wrappers:
            corpus.append((kind, wrap(secret)))
    return corpus


def clean_corpus() -> list[dict[str, Any]]:
    adjectives = [
        "calm",
        "clear",
        "steady",
        "focused",
        "careful",
        "simple",
        "active",
        "ready",
        "direct",
        "useful",
    ]
    nouns = [
        "artifact",
        "review",
        "payload",
        "report",
        "project",
        "workspace",
        "warning",
        "output",
        "context",
        "system",
    ]
    corpus: list[dict[str, Any]] = []
    for index in range(500):
        adjective = adjectives[index % len(adjectives)]
        noun = nouns[(index // len(adjectives)) % len(nouns)]
        corpus.append(
            {
                "description": (
                    f"{adjective} {noun} update {index} keeps the coding task review "
                    f"flow visible and valid."
                ),
                "notes": [f"artifacts/tests/{noun}-{index}.txt", str(uuid.uuid4())],
            }
        )
    return corpus
