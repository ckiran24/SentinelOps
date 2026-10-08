# Operate and troubleshoot SentinelOps

An API returning 200 is not enough. The API must reach the database, workers must
claim jobs, proposals must remain unchanged between approval and execution, and
the audit trail must record each transition. The local stack demonstrates those
boundaries; production integrations need additional provider-side reconciliation.

## Health and progress

| Signal | Meaning | Limitation |
| --- | --- | --- |
| API `/health/live` | HTTP process responds | Does not prove database or queue availability. |
| API `/health/ready` | API can query the database | Does not prove a worker is consuming jobs. |
| Worker wrapper `/health/live` | Supervised worker process is alive | Does not prove a job is making progress. |
| Queued-job age / expired leases | Work is waiting or stalled | Requires a monitoring query/exporter in production. |
| Incident trace + audit | Stored workflow and authorization history | Needs external retention for stronger tamper resistance. |

The prototype does not ship a worker-heartbeat exporter, Prometheus endpoint or
complete remote OpenTelemetry pipeline. Application logs are structured JSON;
`OTEL_CONSOLE_EXPORT=true` optionally exports workflow spans to console output.
The optional collector is a receiver scaffold, and no traces are automatically
exported to it. Database workflow traces are the UI's current source of step
timing and status.

## Useful local commands

```bash
docker compose ps
docker compose logs --tail=100 api worker migrate
curl --fail http://127.0.0.1:8000/health/ready
python infra/smoke_test.py
```

For database inspection without publishing a database port:

```bash
docker compose exec postgres psql -U sentinelops -d sentinelops
```

If a host tool needs a port, opt into the loopback override:

```bash
docker compose -f compose.yaml -f infra/compose.debug.yaml --profile cache up -d
```

The optional Redis cache is not required to process incidents. Restarting it has
no effect on the database-backed job queue in this version.

## When something fails

| Symptom | What to check | Why |
| --- | --- | --- |
| Browser cannot reach API | Browser-visible `NEXT_PUBLIC_API_URL`; rebuild web; exact `CORS_ORIGINS` | Next public variables are embedded at build time, and CORS matches full origins. |
| Login returns 403 | `DEMO_MODE` and intended deployment identity | Demo login is intentionally unavailable outside explicit demo mode. |
| Login/auth returns 503 | Signing algorithm and verification-key configuration | Authentication fails closed rather than accepting unverifiable tokens. |
| Migration container exits | Migration logs, DB credentials, extension permissions | API/worker wait for the schema task to succeed. |
| Incident stays investigating/executing | Worker process, job status/lease, adapter timeout | API only queues work; a stalled worker cannot be fixed by reloading the page. |
| API ready becomes 503 | DB availability, network/socket, credential rotation | Readiness checks include a database query. |
| Live action fails | Missing reviewed live adapter | Flags cannot turn a simulated adapter into a production operation. |
| Evaluations show not run | `EVAL_REPORT_PATH`, report existence and readability | The API reads a measured report artifact, not invented percentages. |

Do not repeatedly queue execution or alter database status fields to bypass a
failed approval. Read the incident's trace, proposal, audit and job state first.
Keep the original idempotency key when retrying the same execution request.

## Queue recovery and mutation boundaries

Investigation and execution jobs are stored in PostgreSQL. Worker claims use locks
and leases; explicit retry/recovery logic distinguishes read-only work from
potentially ambiguous mutation outcomes. A process crash during investigation can
be retried within those boundaries. A crash after a live provider changed state but
before the application recorded success cannot be made safe by a database lease
alone. Future adapters must support an upstream idempotency key or reconcile the
actual provider state before retrying. An ambiguous live outcome needs human review.

The same distinction applies during rolling deployments. Even with a maximum of
one worker replica, old and new revisions can overlap during replacement. Locks
prevent concurrent claims of one healthy leased job; they do not constitute a
global exactly-once promise for arbitrary remote APIs.

The worker stops taking new jobs on SIGTERM and attempts to finish the current job.
Container/cloud shutdown deadlines can still interrupt long-running work. Bound
adapter calls with timeouts, make lease duration/renewal consistent with workload
duration, and test interruption at each mutation boundary before integrating a
real remediation tool.

## Approvals and audit

Approve only after reviewing evidence and typed tool arguments. Approval records
the exact proposal fingerprint. Execution independently checks the caller's role,
approval, fingerprint, policy and idempotency key. Approval itself does not execute.
Rejecting a proposal leaves a reviewable record rather than silently changing it.

Audit events are hash chained to expose changes/reordering during verification.
PostgreSQL triggers reject update, delete and truncate operations on audit events
through the normal database path. A privileged database administrator can disable
those controls, rewrite the events and recompute the
chain. For production, export to independently controlled append-only storage with
retention/immutability controls, record external chain checkpoints, restrict DDL
and privileged database access, and alert on missing exports. Those controls are
deployment work and are not claimed by the local hash chain.

Avoid logging bearer tokens, signing keys, database passwords, raw credentials or
unnecessary incident/log personal data. Apply retention/redaction policies to
evidence, reports and audit details. Local container logs are diagnostic output,
not durable audit retention.

## Backup and restore

The PostgreSQL named volume protects against ordinary container replacement; it
is not a backup. For a local demo, create a custom-format dump and copy it out of
the database container:

```bash
docker compose exec postgres pg_dump -U sentinelops -d sentinelops -Fc -f /tmp/sentinelops.dump
mkdir -p work/backups
docker compose cp postgres:/tmp/sentinelops.dump work/backups/sentinelops.dump
```

For a restore rehearsal, use a separate test database:

```bash
docker compose exec postgres createdb -U sentinelops sentinelops_restore
docker compose cp work/backups/sentinelops.dump postgres:/tmp/sentinelops-restore.dump
docker compose exec postgres pg_restore -U sentinelops -d sentinelops_restore --no-owner /tmp/sentinelops-restore.dump
```

Do not point running production workers at a restored historical queue: old
execution jobs can refer to actions already performed externally. Restore into an
isolated environment with live tools disabled, inspect/reconcile job state and
approvals, and plan the cutover before starting any worker.

In Cloud SQL, configure automated backups and point-in-time recovery according to
the required recovery point/time. Test restoring into a different instance and
verify incident, proposal and audit relationships. Store any exported dumps in
restricted encrypted storage. Document who can restore and how external action
state will be reconciled.

## Release and rollback

1. Run locked dependency installation, security/workflow tests, fixture evaluations,
   TypeScript checks, frontend build and the full-stack smoke test.
2. Build immutable images. Record image digests, migration revision and measured
   fixture-report version. Use explicit Secret Manager versions.
3. Back up the database and inspect schema compatibility. Apply migration once with
   the migration identity before promoting API/worker revisions.
4. Deploy into staging, verify readiness and a role-controlled workflow, then
   promote the exact images/configuration.
5. For a code rollback, restore the previous image revision only if it can read the
   current schema. A container rollback does not undo schema changes or remote
   mutations. Prefer forward fixes/expand-contract migrations over destructive
   downgrades.

The GitHub workflow runs checks and artifacts; it does not deploy, hold cloud
credentials or automatically promote changes to a production service.

## Alerts and capacity decisions for production

Monitor oldest queued job, failed/ambiguous jobs, lease expiration, worker progress,
API errors/latency, database connections/storage/CPU, denied execution spikes and
audit-export lag. Define alert thresholds against the service's incident response
requirements; this prototype has no measured production SLO to copy.

The initial Cloud Run limits are illustrative safety bounds, not load-test results.
Account for API and worker connection pools when choosing database capacity. Use
database/provider timeouts, maximum incident sizes and rate limits. Keep model
token/cost budgets explicit before enabling model calls. Redis can later cache
safe read results or hold rate-limit counters, but it is not currently on the
correctness path and should never be the sole approval record.

Keep the worker's minimum replica and always-allocated CPU configuration in place
while it polls the database. Reducing minimum replicas to zero may stop all work
while API requests still appear successful.
