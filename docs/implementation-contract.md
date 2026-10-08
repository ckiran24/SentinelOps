# Implementation contract

This file coordinates the initial implementation. Public learning documentation is in README.md and docs/.

## Runtime

Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2, PostgreSQL (SQLite local fallback), LangGraph. Backend package is `apps/api/sentinelops` and pyproject.toml lives in apps/api. Start from apps/api: `uvicorn sentinelops.main:app --host 0.0.0.0 --port 8000`. Durable database-backed worker: `python -m sentinelops.worker`. Next.js App Router frontend in apps/web uses NEXT_PUBLIC_API_URL (default http://localhost:8000).

## API

- GET /health/live and GET /health/ready
- POST /api/auth/login `{username,password}` -> `{access_token,token_type,role,username}`. Demo identities controlled by DEMO_MODE. Demo users `operator` and `approver`, password `sentinel-demo`. No signup. JWT secret from configuration.
- GET /api/incidents -> `{items: Incident[], total:number}`
- POST /api/incidents `{title,service,severity,description,scenario}` -> Incident; scenario one of checkout_regression, database_saturation, memory_leak, unknown. Severity SEV1, SEV2, SEV3.
- GET /api/incidents/{id} -> IncidentDetail
- POST /api/incidents/{id}/investigate -> IncidentDetail (queues durable job)
- POST /api/incidents/{id}/approve `{proposal_id,reason}` -> IncidentDetail (approver role only; does not execute)
- POST /api/incidents/{id}/reject `{proposal_id,reason}` -> IncidentDetail (approver role only)
- POST /api/incidents/{id}/execute `{proposal_id,idempotency_key}` -> IncidentDetail (approver role only; queues durable execution)
- GET /api/runbooks -> `{items: [{id,title,service,content}]}`
- GET /api/policy -> `{items:[{tool,risk,approval,description}]}`
- GET /api/evaluations -> measured eval report JSON, or `{status:'not_run',message:...}`. Read EVAL_REPORT_PATH or `../../evals/results/latest.json` from backend cwd.

Incident = `{id,title,service,severity,description,scenario,status,created_at,updated_at}`.
Statuses: new, investigating, awaiting_approval, approved, executing, resolved, rejected, failed, needs_attention.
IncidentDetail adds `hypothesis: {root_cause,confidence,summary}|null`, `evidence:[{id,source,title,content}]`, `proposal: {id,tool,args,risk,requires_approval,status,fingerprint}|null`, `trace:[{step,status,summary,duration_ms}]`, `report:string|null`, `audit:[{id,event,actor,created_at,details,hash}]`.
Root cause vocabulary: deployment_regression, database_saturation, memory_leak, unknown.
Read-only tools: get_recent_deployments, query_service_metrics, search_logs, query_database, get_runbook.
High-risk tools: rollback_deployment, restart_service. Block write_sql, delete_data and unknown tools.

## Safety and persistence

Investigation always ends at policy gate. Approval binds immutable proposal fingerprint; execution independently rechecks role, approval, exact fingerprint, allowlisted typed arguments, and idempotency. Demo mutation only affects simulated state. Live adapters must fail closed until configured. Do not execute model-supplied SQL; accept named read-only query templates. Track append-only hash-chained audit events and document that database administrators can still tamper without external retention. Durable jobs are database-backed; PostgreSQL row locks/leases and retry boundaries must prevent silent replay of real mutations.

## Evaluation

At least 50 synthetic fixtures spanning four causes; expectations and safety adversarial cases. CLI `python -m sentinelops.evaluate --output ../../evals/results/latest.json` runs real workflow (demo/fixture tools), computes measured accuracy, retrieval recall, tool correctness, policy adherence, latency; distinguish simulated remediation and deterministic fixture baseline from real LLM performance. Live OpenAI mode is optional and never claims fixture scores as live-agent scores. Include workflow and approval security tests.

## Packaging

Root compose.yaml runs web, api, worker, PostgreSQL pgvector, Redis and optional telemetry. .env.example, Dockerfiles, CI checks, seed runbooks, architecture and detailed docs. No cloud deployment credentials present; ship concrete deployment guidance and describe production prerequisites honestly.
