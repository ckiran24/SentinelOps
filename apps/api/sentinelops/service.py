import hmac

from fastapi import HTTPException
from sqlalchemy import select, text

from .audit import append_audit
from .db import utc_timestamp, utcnow
from .models import Approval, Audit, Execution, Incident, Job
from .policy import PolicyViolation, authorize_execution, evaluate_proposal
from .schemas import IncidentDetail, IncidentRead


def lock_incident(session, incident_id: str) -> Incident:
    # SQLite has no row locks; serialize local demo mutations at transaction start.
    if session.bind.dialect.name == "sqlite" and not session.in_transaction():
        session.execute(text("BEGIN IMMEDIATE"))
    incident = session.scalar(select(Incident).where(Incident.id == incident_id).with_for_update())
    if incident is None:
        raise HTTPException(404, "Incident not found")
    return incident


def incident_detail(session, incident: Incident) -> dict:
    base = IncidentRead.model_validate(incident).model_dump()
    events = session.scalars(
        select(Audit).where(Audit.incident_id == incident.id).order_by(Audit.sequence)
    ).all()
    return IncidentDetail(
        **base,
        hypothesis=incident.hypothesis,
        evidence=incident.evidence,
        proposal=incident.proposal,
        trace=incident.trace,
        report=incident.report,
        audit=[
            {
                "id": event.id,
                "event": event.event,
                "actor": event.actor,
                "created_at": utc_timestamp(event.created_at),
                "details": event.details,
                "hash": event.hash,
            }
            for event in events
        ],
    ).model_dump()


def proposal_for(incident, proposal_id):
    proposal = incident.proposal
    if not proposal or proposal["id"] != proposal_id:
        raise HTTPException(409, "Proposal does not match the current incident proposal")
    return proposal


def approval_dict(approval: Approval | None) -> dict | None:
    if approval is None:
        return None
    return {
        "proposal_id": approval.proposal_id,
        "fingerprint": approval.fingerprint,
        "approved_by": approval.approved_by,
        "created_at": approval.created_at,
    }


def queue_investigation(session, incident_id, actor):
    incident = lock_incident(session, incident_id)
    if incident.status == "investigating":
        return incident
    execution = session.scalar(select(Execution).where(Execution.incident_id == incident_id))
    if execution or incident.status not in ("new", "failed", "rejected", "needs_attention"):
        raise HTTPException(409, "Incident cannot be reinvestigated in its current state")
    session.add(Job(incident_id=incident.id, kind="investigate", payload={"actor": actor}))
    incident.status, incident.updated_at = "investigating", utcnow()
    append_audit(session, incident.id, "investigation_queued", actor, {"durable": True})
    session.commit()
    return incident


def approve_proposal(session, incident_id, request, actor):
    incident = lock_incident(session, incident_id)
    proposal = proposal_for(incident, request.proposal_id)
    if incident.status != "awaiting_approval" or proposal["status"] != "pending":
        raise HTTPException(409, "Proposal is not awaiting approval")
    try:
        policy = evaluate_proposal(proposal["tool"], proposal["args"])
        if not hmac.compare_digest(policy["fingerprint"], proposal["fingerprint"]):
            raise PolicyViolation("Proposal fingerprint changed")
    except PolicyViolation as exc:
        raise HTTPException(409, str(exc)) from exc
    session.add(
        Approval(
            incident_id=incident.id,
            proposal_id=proposal["id"],
            fingerprint=proposal["fingerprint"],
            approved_by=actor,
            reason=request.reason,
        )
    )
    incident.proposal = {**proposal, "status": "approved"}
    incident.status, incident.updated_at = "approved", utcnow()
    append_audit(
        session,
        incident.id,
        "proposal_approved",
        actor,
        {
            "proposal_id": proposal["id"],
            "fingerprint": proposal["fingerprint"],
            "reason": request.reason,
        },
    )
    session.commit()
    return incident


def reject_proposal(session, incident_id, request, actor):
    incident = lock_incident(session, incident_id)
    proposal = proposal_for(incident, request.proposal_id)
    if incident.status not in ("awaiting_approval", "approved") or proposal["status"] not in (
        "pending",
        "approved",
    ):
        raise HTTPException(409, "Proposal cannot be rejected in its current state")
    incident.proposal = {**proposal, "status": "rejected"}
    incident.status, incident.updated_at = "rejected", utcnow()
    append_audit(
        session,
        incident.id,
        "proposal_rejected",
        actor,
        {"proposal_id": proposal["id"], "reason": request.reason},
    )
    session.commit()
    return incident


def queue_execution(session, incident_id, request, user):
    incident = lock_incident(session, incident_id)
    proposal = proposal_for(incident, request.proposal_id)
    existing = session.scalar(
        select(Execution).where(
            Execution.incident_id == incident.id,
            Execution.idempotency_key == request.idempotency_key,
        )
    )
    if existing:
        if (
            existing.proposal_id != proposal["id"]
            or existing.fingerprint != proposal["fingerprint"]
        ):
            raise HTTPException(409, "Idempotency key belongs to a different exact action")
        return incident
    if session.scalar(select(Execution).where(Execution.proposal_id == proposal["id"])):
        raise HTTPException(
            409, "This proposal already has an execution; reuse its original idempotency key"
        )
    approval = session.scalar(select(Approval).where(Approval.proposal_id == proposal["id"]))
    try:
        authorize_execution(proposal, approval_dict(approval), user.role, request.idempotency_key)
    except PolicyViolation as exc:
        raise HTTPException(409, str(exc)) from exc
    if incident.status != "approved":
        raise HTTPException(409, "Incident must be approved before execution")
    job = Job(
        incident_id=incident.id,
        kind="execute",
        payload={
            "actor": user.username,
            "role": user.role,
            "proposal_id": proposal["id"],
            "fingerprint": proposal["fingerprint"],
            "idempotency_key": request.idempotency_key,
        },
    )
    session.add(job)
    session.flush()
    session.add(
        Execution(
            incident_id=incident.id,
            proposal_id=proposal["id"],
            idempotency_key=request.idempotency_key,
            fingerprint=proposal["fingerprint"],
            job_id=job.id,
        )
    )
    incident.status, incident.updated_at = "executing", utcnow()
    append_audit(
        session,
        incident.id,
        "execution_queued",
        user.username,
        {
            "proposal_id": proposal["id"],
            "fingerprint": proposal["fingerprint"],
            "idempotency_key": request.idempotency_key,
        },
    )
    session.commit()
    return incident
