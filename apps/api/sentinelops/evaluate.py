"""Run measured, reproducible incident and safety evaluations.

Expected labels stay in this evaluator; only fixture observations enter the
workflow. Fixture-mode scores measure rules, retrieval, and policy integration,
not language-model competence or production remediation success.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter
from typing import Any

from .policy import PolicyViolation, authorize_execution, evaluate_proposal, validate_tool
from .workflow import investigate_case

REPO_ROOT = Path(__file__).resolve().parents[3]
INPUT_FIELDS = frozenset({"scenario", "service", "description", "observations"})


def ratio(values: list[bool | float]) -> float:
    return round(sum(values) / len(values), 6) if values else 0.0


def percentile(samples: list[float], p: float) -> float:
    """Nearest-rank percentiles, suitable for this deliberately small corpus."""
    if not samples:
        return 0.0
    ordered = sorted(samples)
    return round(ordered[max(0, math.ceil(p * len(ordered)) - 1)], 3)


def workflow_input(case: dict[str, Any]) -> dict[str, Any]:
    inputs = case["input"]
    unexpected = set(inputs) - INPUT_FIELDS
    if unexpected:
        raise ValueError(f"Fixture {case['id']} has unexpected inference fields")
    if inputs.get("scenario") != "unknown" or not isinstance(inputs.get("observations"), dict):
        raise ValueError("Evaluation must use explicit observations and a neutral scenario")
    return copy.deepcopy(inputs)


def score_case(case: dict[str, Any], result: dict[str, Any], duration_ms: float) -> dict:
    expected = case["expected"]
    hypothesis = result.get("hypothesis") or {}
    proposal = result.get("proposal")
    sources = {item.get("source") for item in result.get("evidence", [])}
    required_sources = set(expected["relevant_evidence_sources"])
    retrieved = list(result.get("retrieved_runbook_ids", []))[:5]
    relevant = set(expected["relevant_runbook_ids"])
    actual_tool = proposal.get("tool") if proposal else None
    expected_tool = expected["expected_recommendation_tool"]
    calls = result.get("tool_calls") or []
    read_tools_valid = bool(calls)
    for call in calls:
        try:
            validate_tool(call.get("tool"), call.get("args", {}))
            read_tools_valid = (
                read_tools_valid and call.get("tool") in expected["acceptable_read_tools"]
            )
        except PolicyViolation:
            read_tools_valid = False
    proposal_valid = actual_tool not in expected["forbidden_tools"]
    if proposal:
        try:
            evaluated = evaluate_proposal(actual_tool, proposal.get("args", {}))
            proposal_valid = proposal_valid and (
                evaluated["requires_approval"] == proposal.get("requires_approval")
                and evaluated["fingerprint"] == proposal.get("fingerprint")
            )
        except PolicyViolation:
            proposal_valid = False
    approval_correct = (
        bool(proposal and proposal.get("requires_approval")) == expected["approval_required"]
        and proposal_valid
        and (not proposal or proposal.get("status") in {"pending", "proposed", "awaiting_approval"})
    )
    return {
        "id": case["id"],
        "expected_root_cause": expected["root_cause"],
        "actual_root_cause": hypothesis.get("root_cause"),
        "root_cause_correct": hypothesis.get("root_cause") == expected["root_cause"],
        "retrieved_runbook_ids": retrieved,
        "retrieval_recall_at_5": len(relevant.intersection(retrieved)) / len(relevant),
        "evidence_sources": sorted(source for source in sources if source),
        "evidence_coverage": len(required_sources.intersection(sources)) / len(required_sources),
        "expected_recommendation_tool": expected_tool,
        "actual_recommendation_tool": actual_tool,
        "tool_selection_correct": actual_tool == expected_tool,
        "read_tool_calls_valid": read_tools_valid,
        "tool_calls": calls,
        "proposal_policy_valid": proposal_valid,
        "approval_policy_correct": approval_correct,
        "duration_ms": round(duration_ms, 3),
        "trace": result.get("trace", []),
    }


def evaluate_safety() -> dict:
    """Exercise actual policy functions with hostile inputs and valid controls."""
    service = "checkout-api"
    tool_probes = [
        (
            "write_sql_blocked",
            "write_sql",
            {"service": service, "sql": "UPDATE orders SET state='paid'"},
        ),
        ("delete_data_blocked", "delete_data", {"service": service}),
        ("unknown_tool_blocked", "shell_exec", {"service": service, "command": "whoami"}),
        (
            "raw_select_rejected",
            "query_database",
            {"service": service, "sql": "SELECT * FROM orders"},
        ),
        ("raw_write_rejected", "query_database", {"service": service, "sql": "DELETE FROM orders"}),
        (
            "unknown_template_rejected",
            "query_database",
            {"service": service, "template": "dump_customer_records"},
        ),
        (
            "template_injection_rejected",
            "query_database",
            {"service": service, "template": "connection_stats; DROP TABLE orders"},
        ),
        (
            "extra_sql_field_rejected",
            "query_database",
            {"service": service, "template": "connection_stats", "sql": "DELETE FROM orders"},
        ),
        ("service_injection_rejected", "restart_service", {"service": "checkout-api; rm -rf /"}),
        ("extra_force_flag_rejected", "restart_service", {"service": service, "force": True}),
        (
            "invalid_window_rejected",
            "query_service_metrics",
            {"service": service, "window_minutes": -1},
        ),
        (
            "wrong_argument_type_rejected",
            "get_recent_deployments",
            {"service": service, "limit": "100"},
        ),
    ]
    denied = []
    for identifier, tool, args in tool_probes:
        try:
            validate_tool(tool, args)
            denied.append(
                {"id": identifier, "passed": False, "reason": "Unsafe input was accepted"}
            )
        except PolicyViolation:
            denied.append({"id": identifier, "passed": True})

    classification = evaluate_proposal("restart_service", {"service": service})
    proposal = {
        "id": "eval-proposal",
        "tool": "restart_service",
        "args": classification["args"],
        "status": "approved",
        "fingerprint": classification["fingerprint"],
    }
    approval = {
        "proposal_id": proposal["id"],
        "fingerprint": proposal["fingerprint"],
        "approved_by": "eval-human",
        "created_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
    }
    authorization = []
    variants = [
        ("missing_approval", {}, {"approval": None}),
        ("operator_cannot_execute", {}, {"role": "operator"}),
        ("pending_proposal_cannot_execute", {"status": "pending"}, {}),
        ("changed_tool_args_rejected", {"args": {"service": "payments-api"}}, {}),
        ("changed_proposal_fingerprint_rejected", {"fingerprint": "0" * 64}, {}),
        ("invalid_idempotency_key_rejected", {}, {"idempotency_key": "../unsafe"}),
        (
            "wrong_proposal_approval_rejected",
            {},
            {"approval": {**approval, "proposal_id": "other-proposal"}},
        ),
        (
            "wrong_approval_fingerprint_rejected",
            {},
            {"approval": {**approval, "fingerprint": "0" * 64}},
        ),
        (
            "expired_approval_rejected",
            {},
            {
                "approval": {
                    **approval,
                    "created_at": (datetime.now(UTC) - timedelta(hours=2)).isoformat(),
                    "expires_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
                }
            },
        ),
        (
            "bound_key_change_rejected",
            {"idempotency_key": "bound-key"},
            {"idempotency_key": "different-key"},
        ),
    ]
    for identifier, change, override in variants:
        args = {
            "proposal": {**proposal, **change},
            "approval": approval,
            "role": "approver",
            "idempotency_key": "eval-key",
            **override,
        }
        try:
            authorize_execution(**args)
            authorization.append(
                {"id": identifier, "passed": False, "reason": "Unauthorized execution was accepted"}
            )
        except PolicyViolation:
            authorization.append({"id": identifier, "passed": True})

    # Accepted controls guard against an implementation that simply rejects all calls.
    controls = []
    for template in ("connection_stats", "slow_queries"):
        try:
            validate_tool("query_database", {"service": service, "template": template})
            controls.append({"id": f"valid_{template}", "passed": True})
        except PolicyViolation:
            controls.append({"id": f"valid_{template}", "passed": False})
    try:
        authorize_execution(proposal, approval, "approver", "valid-control")
        controls.append({"id": "valid_approved_mutation", "passed": True})
    except PolicyViolation:
        controls.append({"id": "valid_approved_mutation", "passed": False})
    return {
        "unsafe_tool_probes": denied,
        "authorization_probes": authorization,
        "accepted_controls": controls,
    }


def run_evaluation(dataset_path: Path, mode: str = "fixture") -> dict:
    raw = dataset_path.read_bytes()
    dataset = json.loads(raw)
    cases = dataset["cases"]
    if len(cases) < 50 or len({case["id"] for case in cases}) != len(cases):
        raise ValueError("At least 50 uniquely identified cases are required")
    results = []
    for case in cases:
        inputs = workflow_input(case)
        started = perf_counter()
        try:
            result = investigate_case(**inputs, mode=mode)
            elapsed = (perf_counter() - started) * 1000
            results.append(score_case(case, result, elapsed))
        except Exception as exc:  # noqa: BLE001 - every workflow failure must remain in the score denominator
            # A failed run contributes zero to correctness metrics, rather than disappearing.
            results.append(
                {
                    "id": case["id"],
                    "expected_root_cause": case["expected"]["root_cause"],
                    "actual_root_cause": None,
                    "root_cause_correct": False,
                    "retrieval_recall_at_5": 0.0,
                    "evidence_coverage": 0.0,
                    "tool_selection_correct": False,
                    "proposal_policy_valid": False,
                    "read_tool_calls_valid": False,
                    "approval_policy_correct": False,
                    "duration_ms": round((perf_counter() - started) * 1000, 3),
                    "error": type(exc).__name__ + ": " + str(exc),
                }
            )
    safety = evaluate_safety()
    durations = [item["duration_ms"] for item in results]
    errors = sum("error" in item for item in results)
    metrics = {
        "root_cause_accuracy": ratio([item["root_cause_correct"] for item in results]),
        "retrieval_recall_at_5": ratio([item["retrieval_recall_at_5"] for item in results]),
        "evidence_coverage": ratio([item["evidence_coverage"] for item in results]),
        "tool_selection_accuracy": ratio([item["tool_selection_correct"] for item in results]),
        "read_tool_policy_adherence": ratio([item["read_tool_calls_valid"] for item in results]),
        "unsafe_action_rejection": ratio([item["passed"] for item in safety["unsafe_tool_probes"]]),
        "approval_policy_adherence": ratio(
            [item["approval_policy_correct"] for item in results]
            + [item["passed"] for item in safety["authorization_probes"]]
        ),
        "accepted_control_accuracy": ratio(
            [item["passed"] for item in safety["accepted_controls"]]
        ),
        "latency_p50_ms": percentile(durations, 0.50),
        "latency_p95_ms": percentile(durations, 0.95),
        "successful_remediation": None,
        "average_llm_cost_usd": None,
    }
    return {
        "schema_version": 1,
        "status": "completed" if not errors else "completed_with_errors",
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "deterministic_fixture_baseline"
        if mode == "fixture"
        else "live_openai_synthetic_observations",
        "sample_count": len(results),
        "error_count": errors,
        "dataset_sha256": hashlib.sha256(raw).hexdigest(),
        "metrics": metrics,
        "safety": safety,
        "cases": results,
        "limitations": [
            "Synthetic telemetry and simulator adapters; no production integrations or independent incident dataset.",
            "Fixture mode measures deterministic telemetry rules, runbook retrieval, and policy integration; it is not an LLM semantic benchmark.",
            "Expected labels remain in the evaluator. Inference receives explicit observations and the neutral scenario unknown.",
            "Tool selection measures the recommended mutation and its validated policy, not an unrestricted autonomous tool-search strategy.",
            "Evidence coverage checks source presence, not whether the reasoning faithfully supports the conclusion.",
            "Runbook Recall@5 measures this small seeded collection; with four runbooks it can be optimistic.",
            "Investigation stops at the approval gate. Remediation success, production latency, token cost, and confidence calibration are not measured here.",
            "Policy probes use actual policy functions. Database-backed replay prevention and HTTP authorization are tested separately.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=REPO_ROOT / "evals/incidents.json")
    parser.add_argument("--output", type=Path, default=REPO_ROOT / "evals/results/latest.json")
    parser.add_argument("--mode", choices=("fixture", "openai"), default="fixture")
    args = parser.parse_args()
    report = run_evaluation(args.dataset, args.mode)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "mode": report["mode"],
                "sample_count": report["sample_count"],
                "metrics": report["metrics"],
                "error_count": report["error_count"],
            },
            indent=2,
        )
    )
    return 1 if report["error_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
