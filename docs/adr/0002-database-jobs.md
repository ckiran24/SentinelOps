# ADR 0002: database-backed work requests

Status: accepted for the initial implementation.

An investigation must survive API restarts and a human approval may arrive much later. We persist jobs alongside incident state in PostgreSQL and run a separate worker. Redis remains optional cache infrastructure.

Writing the incident transition and work request transactionally avoids the early dual-write problem of writing a database row and separately publishing to a broker. It also reduces local infrastructure requirements.

Consequence: monitor database contention, connection pools, job queue age and leases. SQLite is a single-worker learning fallback. At higher scale, a managed queue may be introduced with a transactional outbox and consumers that tolerate duplicates. Mutating work is not blindly replayed after an ambiguous worker failure.
