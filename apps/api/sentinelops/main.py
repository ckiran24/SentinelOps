import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .audit import append_audit, verify_audit_chain
from .auth import LoginRequest, Principal, current_user, login, require_role
from .config import get_settings
from .db import Base, SessionLocal, engine, get_db
from .models import Audit, Incident, Runbook
from .observability import configure_logging, configure_telemetry
from .policy import HIGH_RISK_TOOLS, READ_ONLY_TOOLS
from .schemas import ApprovalRequest, ExecuteRequest, IncidentCreate, IncidentDetail, IncidentRead
from .seed import seed_runbooks
from .service import (
    approve_proposal,
    incident_detail,
    queue_execution,
    queue_investigation,
    reject_proposal,
)

configure_logging()
DbSession = Annotated[Session, Depends(get_db)]
Viewer = Annotated[Principal, Depends(current_user)]
Writer = Annotated[Principal, Depends(require_role("operator", "approver"))]
Approver = Annotated[Principal, Depends(require_role("approver"))]


@asynccontextmanager
async def lifespan(_app):
    settings = get_settings()
    configure_telemetry("sentinelops-api")
    # Production schema changes are an explicit migration job, not API startup side effects.
    if settings.demo_mode:
        Base.metadata.create_all(engine)
        with SessionLocal() as session:
            seed_runbooks(session)
    yield


app = FastAPI(
    title="SentinelOps API",
    version="0.1.0",
    lifespan=lifespan,
    description="Evidence-backed investigation with exact human approval and simulated remediation.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip() for origin in get_settings().cors_origins.split(",") if origin.strip()
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.get("/health/live")
def liveness():
    return {"status": "ok"}


@app.get("/health/ready")
def readiness():
    try:
        get_settings().verification_key()
        with SessionLocal() as session:
            session.execute(text("SELECT 1"))
            session.execute(select(Incident.id).limit(1))
    except Exception as exc:
        logging.getLogger(__name__).warning("Readiness check failed: %s", type(exc).__name__)
        raise HTTPException(
            503, "Database schema or authentication configuration not ready"
        ) from exc
    return {
        "status": "ready",
        "demo_mode": get_settings().demo_mode,
        "live_adapters_configured": False,
    }


@app.post("/api/auth/login")
def auth_login(request: LoginRequest):
    return login(request)


@app.get("/api/incidents")
def list_incidents(db: DbSession, _user: Viewer):
    incidents = db.scalars(select(Incident).order_by(Incident.created_at.desc()).limit(200)).all()
    return {
        "items": [IncidentRead.model_validate(item).model_dump() for item in incidents],
        "total": db.scalar(select(func.count()).select_from(Incident)),
    }


@app.post("/api/incidents", response_model=IncidentRead, status_code=201)
def create_incident(request: IncidentCreate, db: DbSession, user: Writer):
    incident = Incident(**request.model_dump())
    db.add(incident)
    db.flush()
    append_audit(
        db,
        incident.id,
        "incident_created",
        user.username,
        {
            "service": incident.service,
            "severity": incident.severity,
            "telemetry_source": "fixture" if get_settings().demo_mode else "unconfigured",
        },
    )
    db.commit()
    return incident


@app.get("/api/incidents/{incident_id}", response_model=IncidentDetail)
def get_incident(incident_id: str, db: DbSession, _user: Viewer):
    incident = db.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(404, "Incident not found")
    return incident_detail(db, incident)


@app.post(
    "/api/incidents/{incident_id}/investigate", response_model=IncidentDetail, status_code=202
)
def investigate(incident_id: str, db: DbSession, user: Writer):
    return incident_detail(db, queue_investigation(db, incident_id, user.username))


@app.post("/api/incidents/{incident_id}/approve", response_model=IncidentDetail)
def approve(incident_id: str, request: ApprovalRequest, db: DbSession, user: Approver):
    return incident_detail(db, approve_proposal(db, incident_id, request, user.username))


@app.post("/api/incidents/{incident_id}/reject", response_model=IncidentDetail)
def reject(incident_id: str, request: ApprovalRequest, db: DbSession, user: Approver):
    return incident_detail(db, reject_proposal(db, incident_id, request, user.username))


@app.post("/api/incidents/{incident_id}/execute", response_model=IncidentDetail, status_code=202)
def execute(incident_id: str, request: ExecuteRequest, db: DbSession, user: Approver):
    return incident_detail(db, queue_execution(db, incident_id, request, user))


@app.get("/api/incidents/{incident_id}/audit/verify")
def audit_verify(incident_id: str, db: DbSession, _user: Viewer):
    if db.get(Incident, incident_id) is None:
        raise HTTPException(404, "Incident not found")
    events = db.scalars(
        select(Audit).where(Audit.incident_id == incident_id).order_by(Audit.sequence)
    ).all()
    return {
        "valid": verify_audit_chain(events),
        "events": len(events),
        "scope": "application hash chain; external retention is required against database administrator tampering",
    }


@app.get("/api/runbooks")
def list_runbooks(db: DbSession, _user: Viewer):
    books = db.scalars(select(Runbook).order_by(Runbook.id)).all()
    return {
        "items": [
            {"id": book.id, "title": book.title, "service": book.service, "content": book.content}
            for book in books
        ]
    }


@app.get("/api/policy")
def policy(_user: Viewer):
    return {
        "items": [
            {
                "tool": name,
                "risk": "high"
                if name in HIGH_RISK_TOOLS
                else "medium"
                if name == "query_database"
                else "low",
                "approval": name in HIGH_RISK_TOOLS,
                "description": "Exact human approval and durable idempotency required"
                if name in HIGH_RISK_TOOLS
                else "Named read-only templates only"
                if name == "query_database"
                else "Bounded read-only observations",
            }
            for name in sorted(READ_ONLY_TOOLS | HIGH_RISK_TOOLS)
        ]
        + [
            {
                "tool": "write_sql",
                "risk": "critical",
                "approval": "blocked",
                "description": "Write SQL is prohibited",
            },
            {
                "tool": "delete_data",
                "risk": "critical",
                "approval": "blocked",
                "description": "Data deletion is prohibited",
            },
        ]
    }


@app.get("/api/evaluations")
def evaluations(_user: Viewer):
    path = Path(get_settings().eval_report_path)
    if not path.is_file():
        return {
            "status": "not_run",
            "message": "Run python -m sentinelops.evaluate to generate measured results.",
        }
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(503, "Evaluation report unavailable") from exc
