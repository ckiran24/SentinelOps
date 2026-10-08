"""Fail-closed tool policy, immutable proposal binding, and execution checks.

This module is deliberately independent of agents, adapters, and persistence.
The worker must call ``authorize_execution`` immediately before invoking a
mutation. Durable idempotency claims and action receipts belong in the database;
a valid key here is necessary, but cannot provide exactly-once execution alone.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class PolicyViolation(ValueError):
    """The requested action does not meet the execution policy."""


ServiceName = Annotated[
    str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")
]
DeploymentId = Annotated[
    str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
]


class ToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    service: ServiceName


class RecentDeploymentsArgs(ToolArgs):
    limit: Annotated[int, Field(ge=1, le=50)] = 5


class ServiceMetricsArgs(ToolArgs):
    window_minutes: Annotated[int, Field(ge=1, le=1440)] = 30


class SearchLogsArgs(ToolArgs):
    query: Annotated[str, Field(max_length=1000)] = ""
    limit: Annotated[int, Field(ge=1, le=100)] = 50


class DatabaseQueryArgs(ToolArgs):
    # These identifiers select adapter-owned parameterized SELECT statements.
    # No SQL text, column name, or arbitrary query can enter this interface.
    template: Literal["connection_stats", "slow_queries"]


class RunbookArgs(ToolArgs):
    query: Annotated[str, Field(max_length=1000)] = ""


class RollbackArgs(ToolArgs):
    deployment_id: DeploymentId


class RestartArgs(ToolArgs):
    pass


READ_ONLY_TOOLS = frozenset(
    {
        "get_recent_deployments",
        "query_service_metrics",
        "search_logs",
        "query_database",
        "get_runbook",
    }
)
HIGH_RISK_TOOLS = frozenset({"rollback_deployment", "restart_service"})
READ_ONLY_QUERY_TEMPLATES = frozenset({"connection_stats", "slow_queries"})
BLOCKED_TOOLS = frozenset({"write_sql", "delete_data"})

_ARG_MODELS: dict[str, type[ToolArgs]] = {
    "get_recent_deployments": RecentDeploymentsArgs,
    "query_service_metrics": ServiceMetricsArgs,
    "search_logs": SearchLogsArgs,
    "query_database": DatabaseQueryArgs,
    "get_runbook": RunbookArgs,
    "rollback_deployment": RollbackArgs,
    "restart_service": RestartArgs,
}
_FINGERPRINT_RE = re.compile(r"^[a-f0-9]{64}$")
_IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def validate_tool(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    """Return strictly typed arguments with defaults, rejecting unknown fields."""
    if not isinstance(tool, str) or tool not in _ARG_MODELS:
        raise PolicyViolation("Tool is blocked or not allowlisted")
    if not isinstance(args, dict):
        raise PolicyViolation("Tool arguments must be an object")
    try:
        return _ARG_MODELS[tool].model_validate(args).model_dump(mode="json")
    except ValidationError as exc:
        # Do not repeat user-supplied argument values in error or audit output.
        errors = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors(include_input=False, include_url=False)
        )
        raise PolicyViolation(f"Invalid arguments: {errors}") from exc


def proposal_fingerprint(tool: str, args: dict[str, Any]) -> str:
    """Hash the tool and canonical, validated arguments, including defaults."""
    normalized = validate_tool(tool, args)
    payload = json.dumps(
        {"tool": tool, "args": normalized},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def evaluate_proposal(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    """Validate and classify a proposal without executing any tool."""
    normalized = validate_tool(tool, args)
    requires_approval = tool in HIGH_RISK_TOOLS
    return {
        "risk": "high" if requires_approval else "medium" if tool == "query_database" else "low",
        "requires_approval": requires_approval,
        "fingerprint": proposal_fingerprint(tool, normalized),
        "args": normalized,
    }


def _timestamp(value: Any, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise PolicyViolation(f"Approval {field} is not a valid timestamp") from exc
    else:
        raise PolicyViolation(f"Approval {field} is required")
    # SQLite's DateTime serialization drops tzinfo; application timestamps use UTC.
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _check_fingerprint(actual: str, claimed: Any, label: str) -> None:
    if (
        not isinstance(claimed, str)
        or not _FINGERPRINT_RE.fullmatch(claimed)
        or not hmac.compare_digest(actual, claimed)
    ):
        raise PolicyViolation(f"{label} fingerprint does not match the exact proposal")


def authorize_execution(
    proposal: dict[str, Any],
    approval: dict[str, Any] | None,
    role: str,
    idempotency_key: str,
) -> dict[str, Any]:
    """Revalidate an approved mutation immediately before invoking its adapter.

    ``approved_by`` identifies the human approver. The API persists that identity
    from its authenticated principal, never from a request body. If an explicit
    approval role is persisted, this function also checks it. Proposal status
    must still be approved; an already executing/resolved proposal is not a new
    authorization. The caller must atomically claim the validated key in durable
    storage to prevent concurrent execution or mutation replay after a crash.
    """
    if role != "approver":
        raise PolicyViolation("Execution requires the approver role")
    if not isinstance(idempotency_key, str) or not _IDEMPOTENCY_RE.fullmatch(idempotency_key):
        raise PolicyViolation("A valid idempotency key of 1–128 safe characters is required")
    if not isinstance(proposal, dict):
        raise PolicyViolation("Proposal is required")
    proposal_id = proposal.get("id")
    if not isinstance(proposal_id, str) or not proposal_id.strip():
        raise PolicyViolation("Proposal ID is required")
    if proposal.get("status") != "approved":
        raise PolicyViolation("Proposal must be approved before execution")
    tool = proposal.get("tool")
    args = validate_tool(tool, proposal.get("args"))
    if tool not in HIGH_RISK_TOOLS:
        raise PolicyViolation("Execution endpoint accepts remediation tools only")
    actual_fingerprint = proposal_fingerprint(tool, args)
    _check_fingerprint(actual_fingerprint, proposal.get("fingerprint"), "Proposal")

    if not isinstance(approval, dict):
        raise PolicyViolation("A persisted human approval is required")
    if approval.get("proposal_id") != proposal_id:
        raise PolicyViolation("Approval belongs to a different proposal")
    _check_fingerprint(actual_fingerprint, approval.get("fingerprint"), "Approval")
    actor = approval.get("approved_by")
    if not isinstance(actor, str) or not actor.strip():
        raise PolicyViolation("Approval must identify its authenticated approver")
    if approval.get("status", "approved") != "approved":
        raise PolicyViolation("Approval is not approved")
    if approval.get("role", "approver") != "approver":
        raise PolicyViolation("Approval requires the approver role")

    now = datetime.now(UTC)
    created_at = _timestamp(approval.get("created_at"), "created_at")
    if created_at > now:
        raise PolicyViolation("Approval creation time is in the future")
    if approval.get("expires_at") is not None:
        expires_at = _timestamp(approval["expires_at"], "expires_at")
        if expires_at <= created_at or expires_at <= now:
            raise PolicyViolation("Approval has expired")

    bound_key = proposal.get("idempotency_key")
    if bound_key is not None and bound_key != idempotency_key:
        raise PolicyViolation("Proposal is bound to a different idempotency key")
    return args
