from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from sentinelops.config import get_settings
from sentinelops.db import SessionLocal
from sentinelops.models import Audit, Execution, Incident, Job
from sqlalchemy import func, select


def drain_jobs():
    """Run durable jobs synchronously without creating a background worker."""
    from sentinelops.worker import claim_next_job, process_job

    for _ in range(10):
        with SessionLocal() as session:
            job = claim_next_job(session, "pytest-worker")
            if job is None:
                return
            process_job(session, job)
    raise AssertionError("Jobs did not drain; possible unintended replay")


def create_and_investigate(client, headers, scenario="checkout_regression"):
    response = client.post(
        "/api/incidents",
        headers=headers["operator"],
        json={
            "title": "Checkout errors rose after a release",
            "service": "checkout-api",
            "severity": "SEV1",
            "description": "Checkout requests now fail; investigate evidence before acting.",
            "scenario": scenario,
        },
    )
    assert response.status_code == 201, response.text
    incident_id = response.json()["id"]
    response = client.post(
        f"/api/incidents/{incident_id}/investigate", headers=headers["operator"]
    )
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "investigating"
    drain_jobs()
    response = client.get(f"/api/incidents/{incident_id}", headers=headers["operator"])
    assert response.status_code == 200
    return response.json()


def approve(client, headers, incident):
    response = client.post(
        f"/api/incidents/{incident['id']}/approve",
        headers=headers["approver"],
        json={
            "proposal_id": incident["proposal"]["id"],
            "reason": "Reviewed release and supporting evidence.",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "approved"
    return response.json()


def execute(client, headers, incident, key="test-execution-01"):
    return client.post(
        f"/api/incidents/{incident['id']}/execute",
        headers=headers["approver"],
        json={"proposal_id": incident["proposal"]["id"], "idempotency_key": key},
    )


@pytest.mark.parametrize(
    "path", ["/api/incidents", "/api/runbooks", "/api/policy", "/api/evaluations"]
)
def test_read_endpoints_require_authentication(client, path):
    assert client.get(path).status_code == 401


@pytest.mark.parametrize("action", ["investigate", "approve", "reject", "execute"])
def test_incident_actions_require_authentication(client, action):
    response = client.post(
        f"/api/incidents/nonexistent/{action}",
        json={
            "proposal_id": "unused",
            "reason": "test reason",
            "idempotency_key": "unused",
        },
    )
    assert response.status_code == 401


def test_create_requires_authentication_and_bad_login_is_denied(client):
    assert client.post("/api/incidents", json={}).status_code == 401
    assert (
        client.post(
            "/api/auth/login", json={"username": "approver", "password": "wrong"}
        ).status_code
        == 401
    )
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 200


@pytest.mark.parametrize("action", ["approve", "reject", "execute"])
def test_operator_cannot_grant_or_use_approval(client, credentials, action):
    incident = create_and_investigate(client, credentials)
    body = {"proposal_id": incident["proposal"]["id"]}
    body.update(
        {"idempotency_key": "role-check"}
        if action == "execute"
        else {"reason": "Operator attempted approval."}
    )
    response = client.post(
        f"/api/incidents/{incident['id']}/{action}",
        headers=credentials["operator"],
        json=body,
    )
    assert response.status_code == 403
    with SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(Execution)) == 0


def test_execution_before_approval_is_denied(client, credentials):
    incident = create_and_investigate(client, credentials)
    assert incident["status"] == "awaiting_approval"
    response = execute(client, credentials, incident)
    assert response.status_code == 409
    with SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(Execution)) == 0


def test_approval_alone_does_not_schedule_execution(client, credentials):
    incident = approve(client, credentials, create_and_investigate(client, credentials))
    with SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(Execution)) == 0
        assert (
            session.scalar(
                select(func.count()).select_from(Job).where(Job.kind == "execute")
            )
            == 0
        )
    assert "proposal_approved" in {event["event"] for event in incident["audit"]}


def test_wrong_proposal_cannot_be_approved_and_body_cannot_forge_actor(
    client, credentials
):
    incident = create_and_investigate(client, credentials)
    path = f"/api/incidents/{incident['id']}/approve"
    response = client.post(
        path,
        headers=credentials["approver"],
        json={"proposal_id": "different", "reason": "Reviewed evidence."},
    )
    assert response.status_code == 409
    response = client.post(
        path,
        headers=credentials["approver"],
        json={
            "proposal_id": incident["proposal"]["id"],
            "reason": "Reviewed evidence.",
            "approved_by": "administrator",
        },
    )
    assert response.status_code == 422


def test_rejection_revokes_execution_even_after_previous_approval(client, credentials):
    incident = approve(client, credentials, create_and_investigate(client, credentials))
    response = client.post(
        f"/api/incidents/{incident['id']}/reject",
        headers=credentials["approver"],
        json={
            "proposal_id": incident["proposal"]["id"],
            "reason": "New evidence requires manual investigation.",
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    assert execute(client, credentials, incident).status_code == 409


def test_changed_arguments_after_approval_cannot_execute(client, credentials):
    incident = approve(client, credentials, create_and_investigate(client, credentials))
    with SessionLocal() as session:
        persisted = session.get(Incident, incident["id"])
        persisted.proposal = {
            **persisted.proposal,
            "args": {**persisted.proposal["args"], "service": "payments-api"},
        }
        session.commit()
    response = execute(client, credentials, incident)
    assert response.status_code == 409
    assert "fingerprint" in response.json()["detail"].lower()


def test_duplicate_idempotency_is_one_execution_and_different_key_is_denied(
    client, credentials, monkeypatch
):
    from sentinelops.adapters import FixtureAdapter

    calls = []
    original = FixtureAdapter.simulate_mutation

    def counted(adapter, tool, args):
        calls.append((tool, args))
        return original(adapter, tool, args)

    monkeypatch.setattr(FixtureAdapter, "simulate_mutation", counted)
    incident = approve(client, credentials, create_and_investigate(client, credentials))
    assert execute(client, credentials, incident).status_code == 202
    assert execute(client, credentials, incident).status_code == 202
    assert execute(client, credentials, incident, "different-key").status_code == 409
    with SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(Execution)) == 1
        assert (
            session.scalar(
                select(func.count()).select_from(Job).where(Job.kind == "execute")
            )
            == 1
        )
    drain_jobs()
    result = client.get(
        f"/api/incidents/{incident['id']}", headers=credentials["operator"]
    ).json()
    assert result["status"] == "resolved"
    assert len(calls) == 1
    assert execute(client, credentials, incident).status_code == 202
    drain_jobs()
    assert len(calls) == 1
    verification = client.get(
        f"/api/incidents/{incident['id']}/audit/verify", headers=credentials["operator"]
    )
    assert verification.json()["valid"] is True
    assert verification.json()["events"] >= 5


def test_worker_rechecks_proposal_after_queueing(client, credentials, monkeypatch):
    from sentinelops.adapters import FixtureAdapter

    incident = approve(client, credentials, create_and_investigate(client, credentials))
    assert execute(client, credentials, incident).status_code == 202
    with SessionLocal() as session:
        persisted = session.get(Incident, incident["id"])
        persisted.proposal = {
            **persisted.proposal,
            "args": {**persisted.proposal["args"], "service": "payments-api"},
        }
        session.commit()
    calls = []
    monkeypatch.setattr(
        FixtureAdapter, "simulate_mutation", lambda *args: calls.append(args)
    )
    drain_jobs()
    assert calls == []
    result = client.get(
        f"/api/incidents/{incident['id']}", headers=credentials["operator"]
    ).json()
    assert result["status"] in {"failed", "needs_attention"}


def test_failed_verification_does_not_mark_incident_resolved(
    client, credentials, monkeypatch
):
    from sentinelops.adapters import FixtureAdapter

    original = FixtureAdapter.simulate_mutation

    def unhealthy_result(adapter, tool, args):
        result = original(adapter, tool, args)
        result["metrics_after"]["error_rate"] = 0.40
        return result

    monkeypatch.setattr(FixtureAdapter, "simulate_mutation", unhealthy_result)
    incident = approve(client, credentials, create_and_investigate(client, credentials))
    assert execute(client, credentials, incident).status_code == 202
    drain_jobs()
    result = client.get(
        f"/api/incidents/{incident['id']}", headers=credentials["operator"]
    ).json()
    assert result["status"] == "needs_attention"
    assert "verification" in (result["report"] or "").lower()


def test_expired_and_wrong_audience_tokens_are_rejected(client):
    settings = get_settings()
    now = datetime.now(UTC)
    base = {
        "sub": "approver",
        "role": "approver",
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(minutes=10),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
    }
    for change in ({"exp": now - timedelta(seconds=1)}, {"aud": "another-service"}):
        token = jwt.encode({**base, **change}, settings.jwt_secret, algorithm="HS256")
        assert (
            client.get(
                "/api/incidents", headers={"Authorization": "Bearer " + token}
            ).status_code
            == 401
        )


def test_application_orm_cannot_modify_audit_events(client, credentials):
    incident = create_and_investigate(client, credentials)
    with SessionLocal() as session:
        event = session.scalar(select(Audit).where(Audit.incident_id == incident["id"]))
        event.actor = "forged-actor"
        with pytest.raises(ValueError, match="append-only"):
            session.commit()
        session.rollback()
