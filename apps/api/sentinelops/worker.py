"""Durable database job worker with leases and no automatic mutation replay."""

from __future__ import annotations

import logging
import signal
import time
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select, text

from .adapters import FixtureAdapter
from .audit import append_audit
from .config import get_settings
from .db import SessionLocal, utcnow
from .models import Approval, Execution, Incident, Job
from .observability import configure_logging, configure_telemetry
from .policy import PolicyViolation, authorize_execution
from .service import approval_dict, lock_incident
from .workflow import investigate_case

logger = logging.getLogger("sentinelops.worker")
STOP_REQUESTED = False


def _begin_write(session):
    if session.bind.dialect.name == "sqlite" and not session.in_transaction():
        session.execute(text("BEGIN IMMEDIATE"))


def recover_expired_jobs(session):
    """A lost mutation lease is ambiguous even if the external action returned."""
    _begin_write(session)
    expired = session.scalars(
        select(Job)
        .where(Job.state == "running", Job.lease_expires_at < utcnow())
        .with_for_update(skip_locked=True)
    ).all()
    for job in expired:
        incident = lock_incident(session, job.incident_id)
        if job.kind == "execute":
            job.state, job.error = (
                "needs_attention",
                "Execution lease expired; outcome is ambiguous. Automatic replay prohibited.",
            )
            incident.status, incident.updated_at = "needs_attention", utcnow()
            execution = session.scalar(select(Execution).where(Execution.job_id == job.id))
            if execution:
                execution.status = "needs_attention"
            append_audit(
                session,
                incident.id,
                "execution_outcome_ambiguous",
                "worker",
                {"job_id": job.id, "reason": job.error},
            )
        elif job.attempts < 3:
            job.state, job.lease_owner, job.lease_expires_at = "queued", None, None
            job.available_at = utcnow() + timedelta(seconds=2**job.attempts)
            append_audit(
                session,
                incident.id,
                "investigation_retry_scheduled",
                "worker",
                {"job_id": job.id, "attempts": job.attempts, "reason": "Expired lease"},
            )
        else:
            job.state, job.error = "failed", "Investigation exceeded retry limit after lease expiry"
            incident.status, incident.updated_at = "failed", utcnow()
            append_audit(
                session,
                incident.id,
                "investigation_failed",
                "worker",
                {"job_id": job.id, "reason": job.error},
            )
    session.commit()


def claim_next_job(session, owner: str = "test-worker") -> Job | None:
    _begin_write(session)
    job = session.scalar(
        select(Job)
        .where(Job.state == "queued", Job.available_at <= utcnow())
        .order_by(Job.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if job is None:
        session.rollback()
        return None
    job.state, job.lease_owner = "running", owner
    job.lease_expires_at = utcnow() + timedelta(seconds=get_settings().job_lease_seconds)
    job.attempts += 1
    session.commit()
    return job


def _owned_job(session, job_id, owner):
    job = session.scalar(select(Job).where(Job.id == job_id).with_for_update())
    if job is None or job.state != "running" or job.lease_owner != owner:
        raise PolicyViolation("Worker no longer owns this job lease")
    deadline = job.lease_expires_at
    if deadline is None or deadline.replace(tzinfo=None) <= utcnow().replace(tzinfo=None):
        raise PolicyViolation("Job lease has expired")
    return job


def _fixture_only():
    settings = get_settings()
    if not settings.demo_mode or settings.live_tools_enabled:
        raise PolicyViolation(
            "Live infrastructure adapters are not configured. Execution blocked; DEMO_MODE is required for simulated tools."
        )


def verify_result(result: dict) -> bool:
    """Check explicit simulated post-action metrics, not an adapter success flag."""
    metrics = result["metrics_after"]
    recovered = float(metrics.get("error_rate", 1)) < 0.02
    if result["tool"] == "restart_service":
        recovered = recovered and float(metrics.get("memory_percent", 100)) < 75
    return recovered


def _investigate(session, job):
    _fixture_only()
    incident = session.get(Incident, job.incident_id)
    scenario, service, description, job_id, owner = (
        incident.scenario,
        incident.service,
        incident.description,
        job.id,
        job.lease_owner,
    )
    session.rollback()  # Do not hold an operational transaction during analysis/API calls.
    result = investigate_case(scenario, service, description)
    _begin_write(session)
    current = _owned_job(session, job_id, owner)
    incident = lock_incident(session, current.incident_id)
    if incident.status != "investigating":
        raise PolicyViolation("Incident state changed while investigation was running")
    incident.hypothesis, incident.evidence = result["hypothesis"], result["evidence"]
    incident.proposal, incident.trace, incident.report = (
        result["proposal"],
        result["trace"],
        result["report"],
    )
    incident.status = "awaiting_approval" if incident.proposal else "needs_attention"
    incident.updated_at, current.state = utcnow(), "completed"
    append_audit(
        session,
        incident.id,
        "investigation_completed",
        "worker",
        {
            "job_id": current.id,
            "root_cause": incident.hypothesis["root_cause"],
            "proposal_fingerprint": incident.proposal["fingerprint"] if incident.proposal else None,
            "mode": get_settings().llm_mode,
            "telemetry_source": "fixture",
        },
    )
    session.commit()


def _execute(session, job):
    _fixture_only()
    owner, job_id = job.lease_owner, job.id
    session.rollback()
    _begin_write(session)
    current = _owned_job(session, job_id, owner)
    incident = lock_incident(session, current.incident_id)
    payload, proposal = current.payload, incident.proposal
    approval = session.scalar(
        select(Approval).where(Approval.proposal_id == payload["proposal_id"])
    )
    execution = session.scalar(
        select(Execution).where(Execution.job_id == current.id).with_for_update()
    )
    if execution is None or execution.status != "queued":
        raise PolicyViolation("Execution receipt is missing or already started; replay prohibited")
    if (
        incident.status != "executing"
        or not proposal
        or proposal["id"] != payload["proposal_id"]
        or proposal["fingerprint"] != payload["fingerprint"]
        or execution.fingerprint != payload["fingerprint"]
    ):
        raise PolicyViolation("Queued action no longer matches the immutable approved proposal")
    args = authorize_execution(
        proposal, approval_dict(approval), payload["role"], payload["idempotency_key"]
    )
    tool, scenario = proposal["tool"], incident.scenario
    execution.status = "started"
    incident.proposal = {**proposal, "status": "executing"}
    append_audit(
        session,
        incident.id,
        "execution_started",
        payload["actor"],
        {
            "job_id": current.id,
            "proposal_id": proposal["id"],
            "fingerprint": proposal["fingerprint"],
            "simulated": True,
        },
    )
    session.commit()  # Durable marker BEFORE touching the adapter; crashes require reconciliation.

    started = time.perf_counter()
    result = FixtureAdapter(scenario).simulate_mutation(tool, args)
    metrics = result["metrics_after"]
    verified = verify_result(result)
    _begin_write(session)
    current = _owned_job(session, job_id, owner)
    incident = lock_incident(session, current.incident_id)
    execution = session.scalar(
        select(Execution).where(Execution.job_id == current.id).with_for_update()
    )
    if execution.status != "started":
        raise PolicyViolation("Execution receipt state changed; reconciliation required")
    result["verified"] = verified
    execution.status, execution.result = "verified" if verified else "needs_attention", result
    current.state = "completed" if verified else "needs_attention"
    incident.status = "resolved" if verified else "needs_attention"
    incident.proposal = {
        **incident.proposal,
        "status": "executed" if verified else "needs_attention",
    }
    incident.updated_at = utcnow()
    incident.trace = incident.trace + [
        {
            "step": "execute",
            "status": "completed",
            "summary": f"Simulated {tool}; no live infrastructure contacted.",
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        },
        {
            "step": "verify",
            "status": "completed" if verified else "failed",
            "summary": "Simulated telemetry meets recovery thresholds."
            if verified
            else "Recovery thresholds were not met; human follow-up required.",
            "duration_ms": 0,
        },
        {
            "step": "report",
            "status": "completed",
            "summary": "Persisted simulated outcome and follow-up requirements.",
            "duration_ms": 0,
        },
    ]
    incident.report = (
        (incident.report or "")
        + f"\nSimulated action: {tool}\nVerification: {'passed' if verified else 'failed'}\nPost-action fixture metrics: {metrics}\nHuman approver: {approval.approved_by}\nProduction recovery and incident resolution have not been tested.\n"
    )
    append_audit(
        session,
        incident.id,
        "execution_verified" if verified else "execution_verification_failed",
        "worker",
        {"job_id": current.id, "simulated": True, "verified": verified, "metrics_after": metrics},
    )
    session.commit()


def process_job(session, job: Job) -> bool:
    """Process one already claimed job; useful for deterministic integration tests."""
    job_id, owner, kind = job.id, job.lease_owner, job.kind
    try:
        if kind == "investigate":
            _investigate(session, job)
        elif kind == "execute":
            _execute(session, job)
        else:
            raise PolicyViolation("Unknown durable job type")
        return True
    except Exception as exc:  # noqa: BLE001 - durable failure boundary records every adapter error
        session.rollback()
        _begin_write(session)
        current = session.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if current is None or current.state != "running" or current.lease_owner != owner:
            session.rollback()
            logger.warning("Job %s lost ownership; no result committed", job_id)
            return False
        incident = lock_incident(session, current.incident_id)
        error = f"{type(exc).__name__}: {str(exc)[:500]}"
        current.error = error
        if kind == "execute":
            execution = session.scalar(select(Execution).where(Execution.job_id == current.id))
            ambiguous = execution is not None and execution.status == "started"
            current.state = incident.status = "needs_attention" if ambiguous else "failed"
            if execution:
                execution.status = current.state
            if incident.proposal:
                incident.proposal = {**incident.proposal, "status": current.state}
        elif current.attempts < 3 and not isinstance(exc, PolicyViolation):
            current.state = "queued"
            current.available_at = utcnow() + timedelta(seconds=2**current.attempts)
            current.lease_owner, current.lease_expires_at = None, None
        else:
            current.state = incident.status = "failed"
        incident.updated_at = utcnow()
        append_audit(
            session,
            incident.id,
            "job_failed" if current.state != "queued" else "job_retry_scheduled",
            "worker",
            {"job_id": current.id, "kind": kind, "state": current.state, "error": error},
        )
        session.commit()
        logger.warning("Job %s: %s", job_id, error)
        return False


def run_once(owner: str = "test-worker") -> bool:
    with SessionLocal() as session:
        recover_expired_jobs(session)
        job = claim_next_job(session, owner)
        if job is None:
            return False
        process_job(session, job)
        return True


def _request_stop(_signum, _frame):
    global STOP_REQUESTED
    STOP_REQUESTED = True


def main():
    configure_logging()
    configure_telemetry("sentinelops-worker")
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    owner = f"worker-{uuid4()}"
    logger.info("Durable worker starting; owner=%s", owner)
    while not STOP_REQUESTED:
        try:
            if not run_once(owner):
                time.sleep(get_settings().worker_poll_seconds)
        except Exception:
            logger.exception("Worker poll failed; database will be retried")
            time.sleep(get_settings().worker_poll_seconds)
    logger.info("Worker stopped after current job boundary")


if __name__ == "__main__":
    main()
