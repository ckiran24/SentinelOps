"""Exercise durable recovery and audit boundaries with isolated databases."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker

from sentinelops import worker
from sentinelops.audit import append_audit, verify_audit_chain
from sentinelops.db import Base, utcnow
from sentinelops.models import Audit, Execution, Incident, Job


@pytest.fixture
def sessions(tmp_path, monkeypatch):
    """Do not replace application SessionLocal or shared evaluation settings."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'worker-reliability.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA journal_mode=WAL")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(
        worker,
        "get_settings",
        lambda: SimpleNamespace(job_lease_seconds=30, demo_mode=True, live_tools_enabled=False),
    )
    yield sessionmaker(engine, expire_on_commit=False)
    engine.dispose()


def add_incident(session, *, status="investigating"):
    incident = Incident(
        title="Checkout error rate increased",
        service="checkout",
        severity="SEV2",
        description="Synthetic incident for worker reliability tests",
        scenario="checkout_regression",
        status=status,
    )
    session.add(incident)
    session.flush()
    return incident


@pytest.mark.parametrize("receipt_status", ["queued", "started"])
def test_expired_execution_is_ambiguous_and_never_replayed(sessions, monkeypatch, receipt_status):
    now = utcnow()
    monkeypatch.setattr(worker, "utcnow", lambda: now)

    def forbidden_adapter(*_args, **_kwargs):
        raise AssertionError("An expired execution must never reach an adapter")

    monkeypatch.setattr(worker, "FixtureAdapter", forbidden_adapter)
    with sessions() as session:
        incident = add_incident(session, status="executing")
        job = Job(
            incident_id=incident.id,
            kind="execute",
            payload={"proposal_id": "proposal-1"},
            state="running",
            attempts=1,
            lease_owner="lost-worker",
            lease_expires_at=now - timedelta(seconds=1),
        )
        session.add(job)
        session.flush()
        receipt = Execution(
            incident_id=incident.id,
            proposal_id="proposal-1",
            idempotency_key="execute-1",
            fingerprint="a" * 64,
            job_id=job.id,
            status=receipt_status,
        )
        session.add(receipt)
        session.commit()

        worker.recover_expired_jobs(session)
        assert job.state == incident.status == receipt.status == "needs_attention"
        assert "Automatic replay prohibited" in job.error
        assert job.attempts == 1
        assert worker.claim_next_job(session, "replacement-worker") is None
        # Even a retained reference in the crashed worker cannot restart the action.
        assert worker.process_job(session, job) is False
        assert job.state == incident.status == receipt.status == "needs_attention"

        worker.recover_expired_jobs(session)
        events = session.scalars(select(Audit).order_by(Audit.sequence)).all()
        assert [item.event for item in events] == ["execution_outcome_ambiguous"]
        assert verify_audit_chain(events)
        assert session.scalar(select(func.count()).select_from(Job)) == 1
        assert session.scalar(select(func.count()).select_from(Execution)) == 1


def test_expired_investigation_retries_with_backoff_then_stops_after_three_attempts(
    sessions, monkeypatch
):
    clock = [utcnow()]
    monkeypatch.setattr(worker, "utcnow", lambda: clock[0])
    with sessions() as session:
        incident = add_incident(session)
        job = Job(
            incident_id=incident.id,
            kind="investigate",
            payload={},
            state="running",
            attempts=1,
            lease_owner="lost-worker",
            lease_expires_at=clock[0] - timedelta(seconds=1),
        )
        session.add(job)
        session.commit()

        for next_attempt, backoff_seconds in [(2, 2), (3, 4)]:
            worker.recover_expired_jobs(session)
            assert job.state == "queued"
            assert job.lease_owner is None and job.lease_expires_at is None
            assert job.available_at.replace(tzinfo=None) == (
                clock[0] + timedelta(seconds=backoff_seconds)
            ).replace(tzinfo=None)
            assert worker.claim_next_job(session, "early-worker") is None

            clock[0] += timedelta(seconds=backoff_seconds)
            claimed = worker.claim_next_job(session, f"worker-{next_attempt}")
            assert claimed.id == job.id
            assert claimed.attempts == next_attempt
            assert claimed.state == "running"
            assert claimed.lease_owner == f"worker-{next_attempt}"
            clock[0] += timedelta(seconds=31)

        worker.recover_expired_jobs(session)
        assert job.state == incident.status == "failed"
        assert job.attempts == 3
        assert "retry limit" in job.error
        assert worker.claim_next_job(session, "fourth-worker") is None
        worker.recover_expired_jobs(session)
        events = session.scalars(select(Audit).order_by(Audit.sequence)).all()
        assert [item.event for item in events] == [
            "investigation_retry_scheduled",
            "investigation_retry_scheduled",
            "investigation_failed",
        ]
        assert verify_audit_chain(events)


def test_concurrent_workers_cannot_claim_the_same_job(sessions):
    with sessions() as session:
        incident = add_incident(session)
        job = Job(incident_id=incident.id, kind="investigate", payload={})
        session.add(job)
        session.commit()
        job_id = job.id

    start = Barrier(2)

    def compete(owner):
        with sessions() as session:
            start.wait(timeout=5)
            claimed = worker.claim_next_job(session, owner)
            return (claimed.id, claimed.lease_owner) if claimed else None

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(compete, owner) for owner in ["worker-a", "worker-b"]]
        results = [future.result(timeout=10) for future in futures]
    successful = [result for result in results if result is not None]
    assert len(successful) == 1
    assert successful[0][0] == job_id
    with sessions() as session:
        persisted = session.get(Job, job_id)
        assert persisted.state == "running"
        assert persisted.attempts == 1
        assert persisted.lease_owner == successful[0][1]
        assert persisted.lease_expires_at is not None


@pytest.mark.parametrize("mutation", ["update", "delete"])
def test_persisted_audit_events_reject_orm_mutation(sessions, mutation):
    with sessions() as session:
        incident = add_incident(session)
        item = append_audit(
            session, incident.id, "incident_created", "operator", {"service": "checkout"}
        )
        session.commit()
        audit_id, original_hash = item.id, item.hash
        if mutation == "update":
            item.details = {"service": "tampered-service"}
        else:
            session.delete(item)
        with pytest.raises(ValueError, match="append-only"):
            session.flush()
        session.rollback()
        persisted = session.get(Audit, audit_id)
        assert persisted.details == {"service": "checkout"}
        assert persisted.hash == original_hash
        assert verify_audit_chain([persisted])


def test_hash_chain_detects_tampered_details_without_writing_them(sessions):
    with sessions() as session:
        incident = add_incident(session)
        append_audit(session, incident.id, "incident_created", "operator", {"service": "checkout"})
        append_audit(session, incident.id, "investigation_queued", "operator", {"durable": True})
        session.commit()
        items = session.scalars(select(Audit).order_by(Audit.sequence)).all()
        assert verify_audit_chain(items)
        # Model database tampering on the in-memory document, without committing it.
        items[0].details["service"] = "tampered-service"
        assert verify_audit_chain(items) is False
        session.rollback()
        pristine = session.scalars(select(Audit).order_by(Audit.sequence)).all()
        assert verify_audit_chain(pristine)
