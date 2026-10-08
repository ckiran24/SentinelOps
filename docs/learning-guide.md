# Build SentinelOps step by step: what to do and why

Read this alongside the code. The order follows the dependencies of the system: understand the problem, define contracts, persist state, investigate, apply policy, involve a human, execute, verify, then operate and evaluate it.

## Step 1 — Define the outcome before choosing tools

The outcome is a trustworthy incident decision with supporting evidence, and a verified action when approved. A fluent chatbot response is not enough. For the example incident, the starting observation is “checkout API errors rose after a release.” A useful ending is either a verified simulated rollback or a clear escalation explaining which evidence is missing.

Write down the actor and permitted action: an operator may submit and investigate; an approver may authorize and execute a high-risk remediation. The first implementation has a single organization and two demo roles. Tenant isolation, enterprise identity lifecycle and service-specific entitlements are production extensions.

**Why:** clear outcomes determine which endpoints, database records and tests matter. They also make it possible to measure correctness rather than judging the agent's writing style.

**Verify:** run the checkout scenario end to end and inspect evidence, approval, execution and verification. A diagnosis without the approval boundary is incomplete.

## Step 2 — Learn the request lifecycle

React renders what the user sees. It calls FastAPI over HTTP with a bearer token. FastAPI validates the body using Pydantic, checks the user's role, opens a database transaction and returns a typed result. The worker handles queued work independently. The browser refreshes the incident while its status is investigating or executing.

The browser cannot be trusted to hide dangerous operations. A disabled button is a usability feature; the API role check is the security control. Generated `/docs` OpenAPI documentation makes the contract inspectable and lets you try the same requests without the UI.

**Why:** a clear request boundary keeps presentation, application rules and persistence separate. Each can change without giving the browser or model more authority.

**Verify:** call an approval endpoint with the operator token. It must be denied even if you write the HTTP request manually.

## Step 3 — Create the repository structure

Keep frontend code in `apps/web`, backend code in `apps/api/sentinelops`, system tests in `tests`, fixtures and measured outputs in `evals`, infrastructure in `infra`, and explanations in `docs`. This is a monorepo: one repository containing several cooperating applications.

The API and worker import the same Python domain logic. They run as separate processes, so shared variables in Python memory do not coordinate them. They share database records instead. Environment files provide configuration without hard-coding provider keys or production addresses.

**Why:** ownership and dependency boundaries are easier to understand when the filesystem reflects them. A monorepo also makes a change to API and UI contract reviewable in one change.

**Verify:** follow [project-structure.md](project-structure.md) to the entrypoints and trace one endpoint to its workflow and database writes.

## Step 4 — Define typed contracts

Incident input has a title, service, severity and description. Proposal arguments have a tool-specific schema. Unknown tools and unexpected fields should be rejected. Structured model output describes a hypothesis; it is not an arbitrary script to execute.

Use closed choices where the domain is bounded: severity, lifecycle states, root-cause categories and tool names. Use free text for human descriptions and supporting evidence. Avoid accepting raw SQL or arbitrary shell commands merely because that gives the agent flexibility.

**Why:** typed boundaries turn malformed output into a visible error rather than a surprising side effect. They also create stable interfaces for the frontend and tests.

**Verify:** try an unknown tool, invalid service identifier, unexpected argument and raw SQL mutation in policy tests. Each should fail before an adapter call.

## Step 5 — Persist incidents and jobs before running work

An incident is a record, not a transient prompt. Persist its status and investigation result. An investigation request creates a durable job. A worker claims available work, performs bounded steps and saves the outcome. Lease/recovery rules decide what happens if it crashes.

Use PostgreSQL in the shared multi-process environment. SQLite is a simple local option for learning with one worker; it does not validate PostgreSQL row-locking behavior or production throughput. Database migrations version schema changes; initial schema creation alone is not a safe upgrade strategy.

**Why:** incidents can last longer than an HTTP request or process lifetime. Durability makes waiting for approval, auditing and recovery possible.

**Verify:** create an incident, stop the worker before processing, restart it and see that the queued investigation still runs. Inspect the job row and incident status if it fails.

## Step 6 — Build read-only tool adapters

The agent needs observations: deployment history, metrics, logs, database diagnostics and runbooks. An adapter translates a typed tool call into a provider request. In this version, fixture adapters return reproducible system observations and simulate mutations. The MCP server exposes a read-only interface; privileged mutation remains behind application approval policy.

For a real integration, define its authentication, timeout, rate limit, data redaction, error shape and idempotency contract. Use read-only credentials for read tools. A tool labeled “read-only” but backed by an administrator database account is not properly isolated.

**Why:** integration behavior is a domain boundary. Keeping it out of the graph nodes makes failures testable and providers replaceable.

**Verify:** invoke a read tool directly in an isolated test, confirm its schema and check that it cannot mutate the fixture. Never use the MCP server as an unauthenticated route around the API.

## Step 7 — Retrieve relevant runbooks

RAG means retrieval-augmented generation: fetch relevant documents, then provide them as context. A runbook helps the hypothesis and recommendation refer to known operational procedures. The local baseline ranks curated in-code runbooks using keywords without requiring paid embeddings; the database has matching seeded records for browsing. PostgreSQL with pgvector provides an extension path for semantic retrieval.

A production ingestion pipeline needs document identifiers, chunk boundaries, source permissions, document versions, embedding model/version, reindex behavior and deletion propagation. An embedding column alone is not a complete RAG system. Never send unrelated secrets or unauthorized documents to the model.

**Why:** retrieval makes recommendations grounded in the organization's procedures and reduces reliance on model memory. It also creates a measurable component: did the expected document appear among the retrieved results?

**Verify:** run retrieval evals and inspect returned runbook IDs. Keep the measured local baseline distinct from a future semantic retrieval benchmark.

## Step 8 — Implement an explicit LangGraph workflow

Use named steps for triage, retrieval, observation collection, hypothesis, supporting evidence and proposal. State passes between steps. The bounded graph ends at its policy gate. Application code routes a supported proposal to approval and routes insufficient evidence to human attention.

The graph describes how investigation proceeds. Durable application records describe where the incident stands. Do not assume choosing LangGraph automatically configures persistent checkpoints, distributed locks or retries. The implementation persists results at job boundaries; a production checkpointing strategy must specify how intermediate steps resume and how duplicate side effects are prevented.

**Why:** explicit steps make behavior inspectable and bounded. You can see which observation produced a recommendation and test specific transitions.

**Verify:** inspect the trace view and compare it to the workflow diagram. The trace should show observations and decisions, not hidden model chain-of-thought.

## Step 9 — Add optional model reasoning

In offline demo mode, diagnosis is deterministic over fixture observations. With the optional OpenAI configuration, the server can ask the Responses API for a structured hypothesis grounded in evidence. Configure the model identifier yourself and keep the API key server-side.

Treat model output as untrusted. Validate categories and confidence fields. The model does not issue an approval, choose a user's role or supply executable SQL. Provider failure must lead to a documented fallback or visible failure; it must never silently permit a mutation.

**Why:** model reasoning can help with ambiguity, while deterministic code protects invariants that must hold regardless of the model's answer.

**Verify:** test without a key first. Then test model mode in staging with recorded token usage and provider errors. This workspace has no configured OpenAI credential; live model behavior is not part of the recorded local validation.

## Step 10 — Enforce policy before asking for approval

Maintain a tool registry with typed arguments and risk levels. Read tools are permitted with their scopes. Restart and rollback require human approval. SQL writes, deletion and unknown tools are blocked. The policy function is ordinary application code, independent of the model prompt.

Prefer named database diagnostic queries over model-written SQL. With named templates, the application controls the SQL and allowed parameters. Production also needs a database account limited to reading the allowed tables; application validation and database privileges work together.

**Why:** an agent can make a plausible but unsafe recommendation. The policy gate prevents that recommendation from becoming a privileged tool call.

**Verify:** adversarial policy cases should include unknown tools, destructive operations, raw SQL, invalid arguments, absent approvals and insufficient roles.

## Step 11 — Bind human approval to an exact proposal

A person must be able to see what will happen: tool, service, target version or restart scope, risk and evidence. Approval records who authorized it and why. A canonical fingerprint binds the decision to the exact action and arguments. Execute is a separate explicit operation.

An approval is not permission for all future actions in that incident. If the proposal changes, request another approval. A production extension should add expiry, environment/service entitlements, separation of duties and policy-defined change windows.

**Why:** “approve the incident” is too broad. The meaningful unit of approval is a specific remediation in a specific context.

**Verify:** mutate a proposal after approval in a test and try to execute. It must fail. Also test duplicate requests, approval races and a wrong-role caller.

## Step 12 — Execute with idempotency and safe recovery

The execution request includes an idempotency key. The API and worker verify current incident state, approval and fingerprint. The simulated adapter records its outcome so repeated requests do not apply a second mutation.

In real infrastructure, an adapter may succeed remotely but fail to return its response. Automatically retrying can duplicate the mutation. Persist intent, pass a provider-supported idempotency key, and reconcile remote state after uncertainty. If you cannot prove whether the action happened, mark the incident for attention rather than pretending it failed safely.

**Why:** distributed systems can produce duplicate delivery and ambiguous outcomes. Exactly-once external execution is not guaranteed by a single application flag.

**Verify:** execute twice using the same key and inspect the mutation ledger and audit trail. Add a provider-specific “response lost after success” integration test before enabling real actions.

## Step 13 — Verify the operational outcome

After a rollback/restart simulation, query the service again. Compare post-action observations with the incident's success criteria. Verification should determine whether error rate or resource pressure improved. An adapter returning “OK” proves that its request succeeded, not that the incident was solved.

Write a report with initial observations, evidence, hypothesis, action, approver, verification and remaining uncertainty. Keep confidential logs out of exports according to the organization's retention and redaction policy.

**Why:** verified outcomes are the business value. A system that performs actions without measuring results can make an incident worse while reporting success.

**Verify:** run the successful scenarios and failure/unknown scenarios; only verified cases may resolve. The fixture verification is a simulation, not evidence of production recovery.

## Step 14 — Add an audit trail and telemetry

Audit events record important decisions. Hash chaining makes edits detectable by an integrity verifier, but it is not absolute immutability: a privileged database user can rewrite events and recompute a chain. Production needs restricted database permissions and external append-only/WORM retention or externally anchored hashes.

Operational telemetry answers a different question: how is the application running? The current workflow persists readable trace entries; OTEL_CONSOLE_EXPORT=true enables SDK console spans. The optional collector is a receiver scaffold; OTLP export and cross-process context propagation are not configured. Production structured logs and OpenTelemetry spans should carry incident/job identifiers, tool names, durations and outcomes, while excluding secrets and raw sensitive content. Configure a collector/exporter and dashboards in the deployment; instrumentation without collection provides incomplete visibility.

**Why:** audits support accountability; telemetry supports debugging and reliability. Mixing them risks either missing decisions or logging confidential data excessively.

**Verify:** trace one incident from its API request to worker completion, inspect the audit actor and run integrity checks. Confirm credentials are absent from logs.

## Step 15 — Evaluate before claiming quality

The supplied synthetic suite records expected causes, evidence, tools, recommendations and approval requirements. The evaluator runs the actual workflow and calculates observed metrics. The console shows measured results only when a report exists.

Separate deterministic fixture accuracy from live-model accuracy. Synthetic latency is not production P95 latency. Simulated remediation is not real rollback reliability. Token cost is unknown unless token usage and model pricing are recorded; do not assign a fictional dollar value.

**Why:** evaluation makes regressions visible and gives an interview discussion evidence. Honest scope matters more than impressive-looking scores.

**Verify:** run the eval command, open `evals/results/latest.json`, inspect failed cases and compare its report to the UI. See [evaluation.md](evaluation.md).

## Step 16 — Package reproducibly

Compose starts the database, API, worker and web app with health checks. The API container and worker container use the same code image and different commands. Named database volumes retain data when containers are recreated. `.env.example` describes expected settings; never commit `.env` secrets.

CI should install known dependencies, run backend safety tests and evals, then type-check/build the frontend. A passing build does not prove production readiness; it proves the configured checks passed for that revision. Treat dependency upgrades as reviewable changes and regenerate locks intentionally.

**Why:** a project that runs only on its author's machine is difficult to review and operate. Repeatable packaging makes demonstration and deployment practical.

**Verify:** use the README quick start and CI workflow. Docker execution may depend on local daemon access; the validation report states what was actually executed in this environment.

## Step 17 — Prepare a real deployment

Choose one cloud first. The documented GCP path maps the web/API to Cloud Run, PostgreSQL to Cloud SQL, credentials to Secret Manager, and the worker to an always-on service or dedicated worker runtime. Optional Redis is managed separately. GitHub Actions should authenticate using workload identity instead of storing a long-lived cloud key.

Before production, implement enterprise OAuth/OIDC with issuer/audience validation and key rotation, service-scoped RBAC, trusted webhook ingestion, migration rollout, backup restore tests, worker lease monitoring, adapter-specific idempotency, egress restrictions, secret rotation, load tests and incident handling. Tenant isolation is necessary if serving multiple customers. Terraform can encode the reviewed cloud resources after the architecture and ownership rules are settled.

**Why:** production readiness is an operational commitment, not a framework list. Provisioning resources does not supply safe permissions, restore procedures or a tested integration.

**Verify:** follow [deployment.md](deployment.md), perform a staging exercise and a database restore drill, and complete the production requirements in the README. No cloud account was provisioned or deployed by this build.
