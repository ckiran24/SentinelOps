"""API timestamps must preserve instants when SQLite drops UTC tzinfo."""

import json
import os
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.encoders import jsonable_encoder
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from sentinelops.audit import append_audit
from sentinelops.db import Base
from sentinelops.models import Audit, Incident
from sentinelops.service import incident_detail


@pytest.fixture
def sqlite_incident(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'timestamps.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    created_at = datetime(2026, 10, 6, 13, 45, 12, 123000, tzinfo=UTC)
    updated_at = created_at + timedelta(seconds=20)
    with sessions() as session:
        incident = Incident(
            title="Synthetic checkout incident",
            service="checkout",
            severity="SEV2",
            description="SQLite API timestamp roundtrip",
            scenario="checkout_regression",
            status="new",
            created_at=created_at,
            updated_at=updated_at,
        )
        session.add(incident)
        session.flush()
        audit = append_audit(session, incident.id, "incident_created", "operator", {})
        audit_at = audit.created_at
        session.commit()
        incident_id, audit_id = incident.id, audit.id
    yield sessions, incident_id, audit_id, (created_at, updated_at, audit_at)
    engine.dispose()


def assert_utc_instant(encoded, expected):
    assert encoded.endswith(("Z", "+00:00"))
    parsed = datetime.fromisoformat(encoded)
    assert parsed.utcoffset() == timedelta(0)
    assert parsed == expected


def test_sqlite_naive_datetime_roundtrip_serializes_explicit_utc(sqlite_incident):
    sessions, incident_id, audit_id, expected = sqlite_incident
    # A new session proves these values were read from SQLite, rather than kept
    # timezone-aware in an identity map after the original insert.
    with sessions() as session:
        incident, audit = session.get(Incident, incident_id), session.get(Audit, audit_id)
        assert incident.created_at.tzinfo is None
        assert incident.updated_at.tzinfo is None
        assert audit.created_at.tzinfo is None
        encoded = jsonable_encoder(incident_detail(session, incident))
    for actual, instant in zip(
        [encoded["created_at"], encoded["updated_at"], encoded["audit"][0]["created_at"]],
        expected,
        strict=True,
    ):
        assert_utc_instant(actual, instant)


def test_aware_driver_datetimes_preserve_their_instants_during_utc_normalization(sqlite_incident):
    sessions, incident_id, audit_id, expected = sqlite_incident
    with sessions() as session:
        incident, audit = session.get(Incident, incident_id), session.get(Audit, audit_id)
        # PostgreSQL drivers may return aware values in the connection timezone.
        # Keep those values in memory so SQLite cannot discard their offsets.
        incident.created_at = expected[0].astimezone(ZoneInfo("Asia/Kolkata"))
        incident.updated_at = expected[1].astimezone(ZoneInfo("America/New_York"))
        audit.created_at = expected[2].astimezone(ZoneInfo("Asia/Kolkata"))
        with session.no_autoflush:
            encoded = jsonable_encoder(incident_detail(session, incident))
        session.rollback()
    for actual, instant in zip(
        [encoded["created_at"], encoded["updated_at"], encoded["audit"][0]["created_at"]],
        expected,
        strict=True,
    ):
        assert_utc_instant(actual, instant)


def test_kolkata_relative_age_is_not_shifted_by_five_and_a_half_hours(sqlite_incident):
    sessions, incident_id, _audit_id, expected = sqlite_incident
    with sessions() as session:
        encoded = jsonable_encoder(incident_detail(session, session.get(Incident, incident_id)))
    client_timezone = ZoneInfo("Asia/Kolkata")
    now = (expected[0] + timedelta(seconds=45)).astimezone(client_timezone)
    parsed = datetime.fromisoformat(encoded["created_at"])
    assert (now - parsed.astimezone(client_timezone)).total_seconds() == 45
    # The former offsetless response was interpreted as browser-local clock time.
    old_local_interpretation = expected[0].replace(tzinfo=client_timezone)
    assert (now - old_local_interpretation).total_seconds() == 45 + 5.5 * 3600


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is optional for browser Date.parse check")
def test_javascript_date_parse_in_kolkata_retains_the_correct_relative_age(sqlite_incident):
    sessions, incident_id, _audit_id, expected = sqlite_incident
    with sessions() as session:
        encoded = jsonable_encoder(incident_detail(session, session.get(Incident, incident_id)))
    payload = {
        "timestamp": encoded["created_at"],
        "formerOffsetlessTimestamp": expected[0].replace(tzinfo=None).isoformat(),
        "now": (expected[0] + timedelta(seconds=45)).isoformat(),
    }
    script = """
    const input = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
    const now = Date.parse(input.now);
    process.stdout.write(JSON.stringify({
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      ageSeconds: (now - Date.parse(input.timestamp)) / 1000,
      formerAgeSeconds: (now - Date.parse(input.formerOffsetlessTimestamp)) / 1000
    }));
    """
    completed = subprocess.run(
        [shutil.which("node"), "-e", script],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=True,
        timeout=10,
        env={**os.environ, "TZ": "Asia/Kolkata"},
    )
    result = json.loads(completed.stdout)
    assert result["timezone"] in {"Asia/Kolkata", "Asia/Calcutta"}
    assert result["ageSeconds"] == 45
    assert result["formerAgeSeconds"] == 45 + 5.5 * 3600
