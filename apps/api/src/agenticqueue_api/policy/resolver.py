"""Policy precedence resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from agenticqueue_api.capability_keys import CapabilityKey

PolicySource = Literal["task", "project", "workspace", "default"]


class PolicyLike(Protocol):
    """Structural type for policy-like payloads."""

    @property
    def name(self) -> str: ...

    @property
    def version(self) -> str: ...

    @property
    def hitl_required(self) -> bool: ...

    @property
    def autonomy_tier(self) -> int: ...

    @property
    def capabilities(self) -> Any: ...

    @property
    def body(self) -> dict[str, Any]: ...


@dataclass(frozen=True)
class ResolvedPolicy:
    """Effective policy after precedence resolution."""

    name: str
    version: str
    hitl_required: bool
    autonomy_tier: int
    capabilities: tuple[CapabilityKey, ...]
    body: dict[str, Any]
    source: PolicySource

    @property
    def validation_mode(self) -> str:
        """Return the validation mode implied by the policy."""

        return "human_review" if self.hitl_required else "autonomous"


def _resolved_policy(
    policy: PolicyLike,
    *,
    source: PolicySource,
) -> ResolvedPolicy:
    capabilities = tuple(policy.capabilities)
    body = dict(policy.body)
    return ResolvedPolicy(
        name=policy.name,
        version=policy.version,
        hitl_required=policy.hitl_required,
        autonomy_tier=policy.autonomy_tier,
        capabilities=capabilities,
        body=body,
        source=source,
    )


def resolve_effective_policy(
    *,
    default_policy: PolicyLike,
    workspace_policy: PolicyLike | None = None,
    project_policy: PolicyLike | None = None,
    task_policy: PolicyLike | None = None,
) -> ResolvedPolicy:
    """Resolve the effective policy using task > project > workspace > default."""

    if task_policy is not None:
        return _resolved_policy(task_policy, source="task")
    if project_policy is not None:
        return _resolved_policy(project_policy, source="project")
    if workspace_policy is not None:
        return _resolved_policy(workspace_policy, source="workspace")
    return _resolved_policy(default_policy, source="default")
