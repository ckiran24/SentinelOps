# ADR 0003: approval authorizes an exact action

Status: accepted for the initial implementation.

The agent may recommend a remediation but cannot authorize it. Rollback and restart proposals require an approver role. Approval stores the exact canonical proposal fingerprint. Execution rechecks identity, state, typed arguments, fingerprint and idempotency, independently of the model and UI.

Write SQL, destructive operations and unknown tools are denied. Database diagnostic tools accept named query templates rather than arbitrary model-produced SQL. MCP exposes read-only tools in the initial server; it does not create a second mutation route.

Consequence: real integrations must preserve this boundary and use scoped credentials. A changed proposal requires new approval. Production should add approval expiry, service/environment entitlements and configurable separation of duties.
