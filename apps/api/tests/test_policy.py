"""Security boundaries are verified separately from workflow behavior."""

from datetime import UTC, datetime, timedelta

import pytest

from sentinelops.policy import (
    PolicyViolation,
    authorize_execution,
    evaluate_proposal,
    proposal_fingerprint,
    validate_tool,
)


def approved_action(tool="rollback_deployment", args=None):
    args = args or {"service": "checkout", "deployment_id": "deploy-previous"}
    evaluation = evaluate_proposal(tool, args)
    proposal = {"id": "proposal-1", "tool": tool, **evaluation, "status": "approved"}
    approval = {
        "proposal_id": proposal["id"],
        "fingerprint": proposal["fingerprint"],
        "approved_by": "approver",
        "created_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
    }
    return proposal, approval


@pytest.mark.parametrize(
    "tool,args,expected",
    [
        ("get_recent_deployments", {"service": "checkout"}, {"service": "checkout", "limit": 5}),
        (
            "query_service_metrics",
            {"service": "checkout"},
            {"service": "checkout", "window_minutes": 30},
        ),
        ("search_logs", {"service": "checkout"}, {"service": "checkout", "query": "", "limit": 50}),
        (
            "query_database",
            {"service": "checkout", "template": "connection_stats"},
            {"service": "checkout", "template": "connection_stats"},
        ),
        ("get_runbook", {"service": "checkout"}, {"service": "checkout", "query": ""}),
    ],
)
def test_read_only_tools_are_typed_and_do_not_require_approval(tool, args, expected):
    assert validate_tool(tool, args) == expected
    evaluation = evaluate_proposal(tool, args)
    assert evaluation["risk"] == ("medium" if tool == "query_database" else "low")
    assert evaluation["requires_approval"] is False


@pytest.mark.parametrize("tool", ["write_sql", "delete_data", "shell", "ROLLBACK_DEPLOYMENT", ""])
def test_blocked_and_unknown_tools_cannot_be_fingerprinted_or_executed(tool):
    with pytest.raises(PolicyViolation):
        evaluate_proposal(tool, {"service": "checkout"})


@pytest.mark.parametrize(
    "args",
    [
        {"service": "checkout", "template": "SELECT * FROM users"},
        {"service": "checkout", "template": "slow_queries", "sql": "DELETE FROM users"},
        {"service": "checkout", "query": "SELECT * FROM users"},
        {"service": "checkout", "template": "delete_rows"},
    ],
)
def test_database_accepts_only_named_read_only_templates(args):
    with pytest.raises(PolicyViolation):
        validate_tool("query_database", args)


@pytest.mark.parametrize(
    "tool,args",
    [
        ("get_recent_deployments", {"service": "checkout", "limit": "5"}),
        ("get_recent_deployments", {"service": "checkout", "limit": True}),
        ("get_recent_deployments", {"service": "checkout", "limit": 100000}),
        ("query_service_metrics", {"service": "checkout", "window_minutes": 0}),
        ("restart_service", {"service": "checkout; rm -rf /"}),
        ("restart_service", {"service": "checkout", "command": "restart"}),
        ("rollback_deployment", {"service": "checkout"}),
        ("rollback_deployment", {"service": "checkout", "deployment_id": "$(whoami)"}),
    ],
)
def test_argument_coercion_unbounded_values_and_extra_fields_are_rejected(tool, args):
    with pytest.raises(PolicyViolation):
        validate_tool(tool, args)


def test_canonical_fingerprint_is_order_and_default_independent_but_binds_all_arguments():
    first = proposal_fingerprint("search_logs", {"service": "checkout", "query": "error"})
    second = proposal_fingerprint(
        "search_logs", {"limit": 50, "query": "error", "service": "checkout"}
    )
    assert first == second
    assert len(first) == 64
    assert first != proposal_fingerprint("search_logs", {"service": "payments", "query": "error"})
    assert first != proposal_fingerprint("search_logs", {"service": "checkout", "query": "timeout"})


def test_exact_approved_remediation_is_authorized():
    proposal, approval = approved_action()
    assert authorize_execution(proposal, approval, "approver", "execute-1") == proposal["args"]
    assert proposal["requires_approval"] is True
    assert proposal["risk"] == "high"


def test_restart_remediation_requires_approval():
    proposal, approval = approved_action("restart_service", {"service": "worker"})
    assert authorize_execution(proposal, approval, "approver", "execute-1") == {"service": "worker"}


@pytest.mark.parametrize("role", ["operator", "admin", "", None])
def test_execution_requires_authenticated_approver_role(role):
    proposal, approval = approved_action()
    with pytest.raises(PolicyViolation, match="approver role"):
        authorize_execution(proposal, approval, role, "execute-1")


def test_execution_requires_persisted_approval():
    proposal, _ = approved_action()
    with pytest.raises(PolicyViolation, match="human approval"):
        authorize_execution(proposal, None, "approver", "execute-1")


@pytest.mark.parametrize("status", ["pending", "rejected", "executing", "resolved"])
def test_unapproved_or_consumed_proposals_are_rejected(status):
    proposal, approval = approved_action()
    proposal["status"] = status
    with pytest.raises(PolicyViolation, match="must be approved"):
        authorize_execution(proposal, approval, "approver", "execute-1")


def test_approval_cannot_be_reused_for_a_different_proposal():
    proposal, approval = approved_action()
    approval["proposal_id"] = "proposal-2"
    with pytest.raises(PolicyViolation, match="different proposal"):
        authorize_execution(proposal, approval, "approver", "execute-1")


@pytest.mark.parametrize(
    "field,value", [("service", "payments"), ("deployment_id", "deploy-other")]
)
def test_modified_arguments_invalidate_approval_even_if_the_new_arguments_are_allowlisted(
    field, value
):
    proposal, approval = approved_action()
    proposal["args"][field] = value
    with pytest.raises(PolicyViolation, match="fingerprint"):
        authorize_execution(proposal, approval, "approver", "execute-1")


def test_replacing_stored_fingerprint_does_not_replace_human_approval():
    proposal, approval = approved_action()
    proposal["args"]["deployment_id"] = "deploy-unapproved"
    proposal["fingerprint"] = proposal_fingerprint(proposal["tool"], proposal["args"])
    with pytest.raises(PolicyViolation, match="Approval fingerprint"):
        authorize_execution(proposal, approval, "approver", "execute-1")


def test_modified_tool_invalidates_approval():
    proposal, approval = approved_action()
    proposal["tool"] = "restart_service"
    proposal["args"] = {"service": "checkout"}
    with pytest.raises(PolicyViolation, match="fingerprint"):
        authorize_execution(proposal, approval, "approver", "execute-1")


@pytest.mark.parametrize(
    "change",
    [{"approved_by": ""}, {"role": "operator"}, {"status": "rejected"}, {"created_at": "invalid"}],
)
def test_invalid_approval_metadata_is_rejected(change):
    proposal, approval = approved_action()
    approval.update(change)
    with pytest.raises(PolicyViolation):
        authorize_execution(proposal, approval, "approver", "execute-1")


def test_expired_approval_is_rejected():
    proposal, approval = approved_action()
    approval["created_at"] = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    approval["expires_at"] = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    with pytest.raises(PolicyViolation, match="expired"):
        authorize_execution(proposal, approval, "approver", "execute-1")


def test_future_approval_is_rejected():
    proposal, approval = approved_action()
    approval["created_at"] = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    with pytest.raises(PolicyViolation, match="future"):
        authorize_execution(proposal, approval, "approver", "execute-1")


def test_valid_optional_expiry_and_sqlite_utc_timestamps_are_supported():
    proposal, approval = approved_action()
    approval["created_at"] = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)
    approval["expires_at"] = datetime.now(UTC) + timedelta(hours=1)
    assert authorize_execution(proposal, approval, "approver", "execute-1") == proposal["args"]


@pytest.mark.parametrize("key", ["", " ", "key with spaces", "$(date)", "a" * 129, None])
def test_invalid_idempotency_keys_are_rejected(key):
    proposal, approval = approved_action()
    with pytest.raises(PolicyViolation, match="idempotency key"):
        authorize_execution(proposal, approval, "approver", key)


def test_proposal_cannot_be_rebound_to_a_different_idempotency_key():
    proposal, approval = approved_action()
    proposal["idempotency_key"] = "execute-1"
    assert authorize_execution(proposal, approval, "approver", "execute-1") == proposal["args"]
    with pytest.raises(PolicyViolation, match="different idempotency key"):
        authorize_execution(proposal, approval, "approver", "execute-2")


def test_read_only_tool_cannot_be_promoted_to_a_remediation():
    proposal, approval = approved_action("get_runbook", {"service": "checkout"})
    with pytest.raises(PolicyViolation, match="remediation tools only"):
        authorize_execution(proposal, approval, "approver", "execute-1")
