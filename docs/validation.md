# Validation record

Recorded on 6 October 2026. This document reports executed checks and their limits, not a production certification.

## Final application checks

| Check | Observed result | What it establishes |
| --- | --- | --- |
| Backend suite: `python -m pytest tests apps/api/tests -q` | **97 passed**, final owner run 2.56 seconds | Policy, API lifecycle, worker recovery and UTC serialization regressions on temporary SQLite databases. |
| Frontend TypeScript | Passed | Frontend source satisfies its configured TypeScript checks. |
| Frontend production build | Passed with Next.js **16.3.8** | Static/application compilation and standalone build succeed. |
| Frontend dependency audit | **0 reported vulnerabilities** | npm's advisory database reported none for the final lockfile at the time of checking. This is not a universal security guarantee. |
| Ruff on backend and tests | Passed | Configured lint/style rules pass. |
| Fixture evaluator | **64 cases, 0 errors** | Real workflow/retrieval/policy code runs on explicit synthetic observations. |
| Native HTTP smoke | Passed | API and a separate worker complete the approval-to-resolution lifecycle with durable SQLite jobs. |
| Browser journey | Passed, no page errors observed | Operator creation/investigation, approver decision/execution, resolution, report, runbooks, policy, measured evals and architecture work through the UI. |
| Mobile browser | Passed at **390 × 844** viewport, no document horizontal overflow | Main console, new incident form, navigation and uncertain-evidence escalation remain usable. |
| Browser timezone | Passed in **Asia/Kolkata** | API UTC timestamps represent the correct instant rather than appearing five and a half hours old. |
| MCP stdio | Handshake, list/read succeeded; rollback denied | The real MCP protocol exposes typed fixture reads and blocks mutation bypass. |
| Python wheel | Built with migration assets | Packaged installation retains Alembic configuration and versions. |

The UTC regression matters: SQLite drops timezone metadata when reading datetimes. The API now attaches UTC to its naive database timestamps and converts aware datetimes to UTC. Tests check actual JavaScript `Date.parse` behavior under Asia/Kolkata, as well as incident and audit serialization.

## Real local container checks

The final Docker Compose stack was built and started in this workspace. PostgreSQL/pgvector, the migration task, the nonroot API, the supervised worker and the Next.js frontend passed their health checks. Validation used ports 8100/3100 to coexist with native browser tests; normal user defaults remain 8000/3000.

The HTTP smoke created a PostgreSQL-backed incident, queued investigation, observed a supported rollback proposal, rejected approval from the operator role, approved through the approver role, explicitly executed the simulation, verified resolution and replayed the same idempotency key safely.

The container evaluator loaded its default dataset and completed 64 fixture cases without errors. The API could read the mounted measured evaluation report. PostgreSQL had its vector extension installed.

Direct PostgreSQL audit guard checks attempted **UPDATE**, **DELETE** and **TRUNCATE** within rolled-back transactions. All three were rejected. The application ORM also blocks audit updates/deletes. Database administrators can still drop defenses or rewrite history; stronger retention requires external anchoring or immutable storage.

Cloud Run templates and their renderer were parsed/rendered locally. They have not been server-validated, applied or deployed to a cloud account. A final API container rebuild includes the UTC serialization patch.

## Evaluation interpretation

The checked-in generated report is `evals/results/latest.json`. Its deterministic fixture baseline produced 1.0 for measured correctness and safety checks. Those results are intentionally labeled **synthetic deterministic integration results**. The classifier and fixture suite were designed together; this is not an independent held-out model benchmark.

The recorded report's local latency P50 is 2.298 ms and P95 is 7.825 ms. Container/native runs can vary with host load. These durations do not include live provider calls, customer integrations or production queues. Retrieval Recall@5 is measured over only four curated runbooks, so it does not establish semantic retrieval quality at enterprise scale.

Remediation success rate and average LLM cost remain null in the evaluation report because the harness stops at the approval gate and does not collect provider billing. Separate lifecycle tests execute simulated remediation. No actual system rollback or restart occurred.

## Recorded evidence

- [Investigation trace](media/agent-trace.png)
- [Approved proposal](media/approved-proposal.png)
- [Resolved incident](media/resolved-incident.png)
- [Incident report](media/incident-report.png)
- [Measured evaluation dashboard](media/evaluation-dashboard.png)
- [Mobile console](media/mobile-console.png)
- [Incident workflow recording](media/incident-demo.webm)

These are captures from the running native demo, using SQLite and synthetic telemetry. They are separate from the real PostgreSQL container validation above.

## What remains unverified or intentionally unfinished

No OpenAI credential was configured, so the optional live-model path was not called. No enterprise provider, production mutation adapter, OAuth/OIDC login flow, tenant isolation, semantic embedding ingestion/search, OTLP export/context propagation, load capacity, managed backup restore, cloud IAM deployment or production recovery was tested.

The worker has bounded leases and protects against ambiguous mutation replay. Long-running future adapters require lease renewal or a reviewed longer-bound execution design. The current fixture mutation and database receipt cannot establish exactly-once behavior for a remote provider.

See [security.md](security.md), [deployment.md](deployment.md), [operations.md](operations.md) and the production requirements in the README before configuring real infrastructure.
