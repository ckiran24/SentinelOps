import hashlib
import json

from sqlalchemy import event, select

from .db import utc_timestamp, utcnow
from .models import Audit, Incident, new_id


def _canonical_time(value) -> str:
    # SQLite drops timezone metadata; persist the same UTC canonical text in both backends.
    aware = utc_timestamp(value)
    return aware.replace(tzinfo=None).isoformat(timespec="microseconds") + "Z"


def event_hash(item) -> str:
    document = {
        "id": item.id,
        "incident_id": item.incident_id,
        "sequence": item.sequence,
        "event": item.event,
        "actor": item.actor,
        "created_at": _canonical_time(item.created_at),
        "details": item.details,
        "previous_hash": item.previous_hash,
    }
    return hashlib.sha256(
        json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def append_audit(session, incident_id: str, event_name: str, actor: str, details: dict) -> Audit:
    # Serialize the chain with the incident row lock. Callers keep this lock through commit.
    session.execute(select(Incident.id).where(Incident.id == incident_id).with_for_update())
    last = session.scalar(
        select(Audit)
        .where(Audit.incident_id == incident_id)
        .order_by(Audit.sequence.desc())
        .limit(1)
    )
    item = Audit(
        id=new_id(),
        incident_id=incident_id,
        sequence=last.sequence + 1 if last else 1,
        event=event_name,
        actor=actor,
        details=details,
        created_at=utcnow(),
        previous_hash=last.hash if last else "0" * 64,
        hash="",
    )
    item.hash = event_hash(item)
    session.add(item)
    session.flush()
    return item


def verify_audit_chain(items) -> bool:
    previous_hash, sequence = "0" * 64, 1
    for item in items:
        if (
            item.sequence != sequence
            or item.previous_hash != previous_hash
            or item.hash != event_hash(item)
        ):
            return False
        previous_hash, sequence = item.hash, sequence + 1
    return True


@event.listens_for(Audit, "before_update")
@event.listens_for(Audit, "before_delete")
def prevent_audit_change(*_):
    raise ValueError("Audit events are append-only through the application ORM")
