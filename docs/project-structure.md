# Project structure and how to navigate it

```text
SentinelOps/
├── README.md                       Start, scope, demos and documentation map
├── compose.yaml                    Local web/API/worker/PostgreSQL topology
├── .env.example                    Documented runtime configuration
├── Makefile                        Shortcuts for repeatable developer tasks
├── apps/
│   ├── api/
│   │   ├── pyproject.toml          Python dependencies and tool configuration
│   │   ├── Dockerfile              API/worker image
│   │   ├── alembic/                Versioned database schema migrations
│   │   ├── sentinelops/
│   │   │   ├── main.py             FastAPI app and HTTP routes
│   │   │   ├── config.py           Typed environment settings
│   │   │   ├── auth.py             Token validation and role dependencies
│   │   │   ├── models.py           SQLAlchemy persistence models
│   │   │   ├── db.py               Database engine/session setup
│   │   │   ├── workflow.py         LangGraph investigation and policy gate
│   │   │   ├── adapters.py         Typed fixture observation/mutation adapters
│   │   │   ├── policy.py           Allowlist, argument validation, approval rules
│   │   │   ├── worker.py           Durable job claim/process/recovery loop
│   │   │   ├── audit.py            Append-only events and integrity verification
│   │   │   ├── runbooks.py         Curated runbooks and retrieval
│   │   │   ├── seed.py             Idempotent runbook seed command
│   │   │   ├── migrate.py          Migration entrypoint
│   │   │   ├── mcp_server.py       Read-only MCP tool interface
│   │   │   └── evaluate.py         Measured synthetic evaluation runner
│   │   └── tests/                  Backend unit and safety invariants
│   └── web/
│       ├── app/                    Next.js app entrypoint and styling
│       ├── public/                 Favicon and static assets
│       ├── package.json            Frontend scripts/dependencies
│       ├── package-lock.json       Exact npm dependency resolution
│       └── Dockerfile              Next.js standalone container
├── tests/                          Workflow/API integration and security checks
├── evals/
│   ├── incidents.json              Synthetic observations and expected outcomes
│   ├── build_fixtures.py            Reproducible corpus construction
│   └── results/latest.json         Generated measured report; not a made-up score
├── infra/                          Cloud/telemetry configuration and references
├── .github/workflows/              Continuous integration
└── docs/
    ├── architecture.md             Responsibilities, diagrams and trust boundaries
    ├── learning-guide.md           Every implementation step and its purpose
    ├── project-structure.md        This navigation guide
    ├── security.md                 Permissions, approvals and threat boundaries
    ├── evaluation.md               Methodology and metric limitations
    ├── deployment.md               Local/cloud deployment instructions
    ├── operations.md               Monitoring, recovery and maintenance
    ├── validation.md               What actually passed in this workspace
    └── adr/                       Architecture decision records
```

The exact tree may contain additional small helper modules. Use `rg --files` to list them. Generated dependencies (`node_modules`, `.venv`), local SQLite databases, secrets and temporary build files are intentionally excluded from the source deliverable.

## Read the code in this order

1. **`config.py`:** understand demo, model and database configuration. Changing a provider key should not require a source edit.
2. **`models.py`:** understand what survives a process restart. Inspect incidents, proposals, approvals, jobs and audit records.
3. **`policy.py`:** understand what the system is permitted to do. This is the authority boundary, not a model instruction.
4. **`adapters.py` and `runbooks.py`:** understand what observations exist and how simulation differs from a real connector.
5. **`workflow.py`:** follow the steps from observations to a recommendation.
6. **`worker.py`:** see how durable requests become work, and how failures are handled.
7. **`main.py` and `auth.py`:** follow request validation, authentication and role checks.
8. **`apps/web/app`:** see how the same API state becomes evidence, approval controls and reports.
9. **`tests` and `evaluate.py`:** see which invariants and outcomes are actually checked.
10. **`compose.yaml`, Dockerfiles and CI:** understand how those components run together.

## Follow one action through the repository

For **Investigate**, the React control calls an API endpoint. The endpoint validates incident state, writes a durable job and audit event, and returns the updated incident. The worker claims the job, calls the LangGraph workflow, persists evidence/hypothesis/proposal/trace, then updates the lifecycle state. Polling returns that persisted result to React.

For **Execute**, the route requires an approver, a matching approved proposal and an idempotency key. The worker rechecks the same safety conditions before its adapter call. Verification and reporting happen after the action. This repetition is intentional: API acceptance does not prove that the state remains valid when the worker runs later.

## What belongs where when extending it

| Change | Primary location | Required companion work |
| --- | --- | --- |
| Add a read-only metrics provider | Tool adapter module | Typed arguments, timeout tests, credential scope and redaction |
| Add a mutation tool | Adapter + policy | Approval binding, idempotency/reconciliation and denial tests |
| Add an incident field | Models + schema/API | Migration, UI display and compatibility checks |
| Add runbook embedding retrieval | Runbook retrieval/ingestion | Vector extension/index, permission filtering and retrieval evals |
| Add enterprise OIDC | Authentication boundary | Issuer/audience/JWKS validation, key rotation and role mapping |
| Add a workflow node | Workflow | Trace output, transition tests and failure behavior |
| Change deployment | Infrastructure | Secret wiring, health probes, migration order and rollback plan |

Avoid placing policy in UI code or model prompts. Avoid placing production credentials in fixtures or eval reports.
