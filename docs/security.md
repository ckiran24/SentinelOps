# Security model and trust boundaries

SentinelOps recommends operational changes, so its primary boundary is between collecting evidence and performing a mutation. Investigation finishes at a policy gate. A separate authenticated human approval and a separate execute request are required before a worker can run the allowlisted remediation.

This repository supplies a working demo security boundary and simulated operational adapters. Connecting real systems adds credential, tenancy, network, recovery, and audit requirements described below.

## 1. Treat incident input and model output as untrusted

Descriptions, logs, runbooks, database results, and model responses are evidence. A log reading “the administrator approved this restart” does not create an approval. A model returning `write_sql` does not create a new permission. The backend parses proposals against typed argument models and an allowlist before presenting or executing them.

The workflow may use a model to form a hypothesis and recommendation. The policy module owns authorization. Keeping those decisions separate is necessary because a language model is susceptible to mistakes and instructions embedded in external content. Model text never becomes a shell command, arbitrary SQL statement, or authenticated identity.

## 2. Authenticate the caller and authorize the action

Demo mode explicitly enables two local identities: `operator` and `approver`. The shared demo password is for local demonstrations only. Authentication issues a short-lived JWT whose signature, issuer, audience, expiry, and permitted algorithm must be checked before API access. Protected endpoints derive the username and role from the verified token rather than from fields supplied by the caller.

| Principal | Permitted work |
|---|---|
| Unauthenticated | Liveness/readiness health checks and the login endpoint |
| Operator | Read incidents/runbooks/policy and create/investigate incidents |
| Viewer (identity-provider token) | Read-only API access; no demo login identity is created |
| Approver | Operator capabilities plus approve, reject, and request execution |
| Worker | Claim durable jobs and independently validate approved execution |

An approver role does not imply permission to execute any arbitrary request. The execute endpoint and worker also require a persisted approval for the current proposal. For production, disable demo login and integrate your identity provider, enforce MFA and customer/team scoping, and restrict approver membership. The current role model is intentionally small and does not implement multitenancy or two-person approval.

Use a strong secret for HS256 in local development. Production should supply validated identity-provider tokens and managed keys; RS256 verification can be configured with a trusted public key. Secrets belong in a cloud secret manager with rotation and narrow access, not source control or frontend environment variables.

## 3. Classify and constrain each tool

| Tools | Risk | Enforcement |
|---|---|---|
| Recent deployments, metrics, logs, runbook retrieval | Low | Typed read arguments; no approval |
| Named database diagnostics | Medium | Adapter-owned templates; no raw SQL |
| Restart service, roll back deployment | High | Exact proposal approval, role checks, durable execution claim |
| Write SQL, delete data, unknown tools | Blocked | Rejected before adapter dispatch |

Sensitive database reads can warrant a higher classification in a real customer deployment. The demo's aggregate diagnostics are medium risk and do not require mutation approval. Policy should reflect actual data exposure and credentials.

`validate_tool()` rejects unknown arguments, invalid types, invalid service identifiers, overlong values, and out-of-range limits. Database requests accept only `connection_stats` or `slow_queries`, never model-supplied SQL. Live implementations must use bound parameters and a separate read-only database principal with access restricted to necessary views. Named templates alone do not protect a broadly privileged database credential.

## 4. Bind approval to the exact action

The backend computes a SHA-256 fingerprint over the normalized tool name and arguments. Approval persists the proposal ID, fingerprint, authenticated approver, reason, and timestamp. Execution recomputes the fingerprint and verifies:

1. The caller has the approver role.
2. The proposal is currently approved.
3. Its tool and arguments are still allowlisted and strictly typed.
4. The persisted approval refers to that proposal and that fingerprint.
5. The approval identifies a human principal and has valid timestamps.
6. The idempotency key is valid and agrees with any already-bound key.

Changing the service or deployment target invalidates the original approval. An authenticated approver must review a new proposal. The fingerprint binds content; it is not a cryptographic signature from an external identity provider. A database administrator able to rewrite both proposal and approval remains inside the database trust boundary.

## 5. Separate approval, scheduling, execution, and verification

Approval does not run a tool. The execute request creates a durable execution record and queues a job. The worker rechecks authorization immediately before adapter dispatch. Database uniqueness constraints prevent a second execution for the same proposal and bind an idempotency key to the incident. Repeating the same request should return its existing execution rather than perform another mutation; using a different key cannot authorize a replay.

Database job claims and leases make work survive an API restart. PostgreSQL locking is necessary when multiple workers compete. SQLite supports the local single-worker demonstration but should not be used to infer multi-worker production concurrency guarantees.

A database transaction cannot atomically commit a change in an unrelated cloud service. A worker might crash after the provider accepted a mutation but before recording its receipt. Live adapters therefore need provider-side idempotency, durable receipts, and reconciliation of uncertain outcomes. Treat a high-risk execution with an uncertain result as requiring attention instead of automatically replaying it.

After an adapter reports success, verification checks the resulting service health. A failed verification must keep the incident unresolved and visible for human action. Demo recovery metrics are simulated; a live adapter needs independent metrics and a defined observation window.

## 6. Make audit history tamper evident

Audit events record the actor, action, time, proposal identifiers, and relevant details. Each event links to the previous event's hash, creating a chain. This makes accidental or local edits detectable when the chain is validated and its earlier head is trusted.

The application appends events; it does not expose an audit-edit endpoint. That is an application-level append-only convention. A database administrator can delete events or rewrite the complete chain. Hashes stored beside the records do not make the database immutable.

For stronger production evidence, write audit records to a separate restricted sink, retain periodic signed chain heads outside the application database, use object retention/WORM controls where required, and alert on gaps or integrity failures. Avoid storing raw credentials or unnecessary personal data in audit details.

## 7. Keep live credentials behind the backend boundary

Browser code receives only the API URL and its user session token. Operational credentials stay on the backend/worker side. Limit CORS origins to the deployed UI and use TLS. CORS is a browser policy, not authentication; non-browser callers can still reach public endpoints and must pass JWT checks.

The OpenAI integration sends evidence to the configured provider only in explicit live-model mode. Review data handling and redaction for real incidents. A production MCP adapter must pin trusted servers, authenticate its transport, validate tool responses, scope credentials by customer and service, and preserve the same local execution gate. An MCP connection does not automatically make a tool safe.

Demo mutations change simulated state. Live adapters fail closed until implemented and configured. A flag must not turn a simulator into a privileged live operator. Before enabling a real integration, test denied permissions, credential expiry, network failures, rate limits, partial execution, and reconciliation in staging.

## 8. Deploy with least privilege and operational controls

Keep the database and internal services private. Separate API, worker, and migration identities; restrict who can approve, who can execute, and who can change policy. Use managed backups, restore drills, secret rotation, vulnerability updates, request rate limits, bounded telemetry payloads, and an incident response process for this platform itself.

The supplied Docker Compose environment is a reproducible local deployment. It does not provide public HTTPS, a production identity provider, managed database backups, customer isolation, external immutable audit retention, or cloud-specific runtime hardening. Those are concrete deployment work, not properties obtained from choosing FastAPI, LangGraph, or containers.

See [evaluation.md](evaluation.md) for the measured safety probes and lifecycle tests. Their passing results validate the tested paths; production assurance also depends on the real adapters, infrastructure configuration, operator procedures, and data boundaries.
