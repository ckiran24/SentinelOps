# ADR 0004: reproducible simulation before live infrastructure

Status: accepted for the initial implementation.

No enterprise metrics, deployments, logs or cloud credentials are configured in this workspace. The initial adapters simulate observations and state changes. This lets reviewers run the complete workflow and test dangerous-action rejection without operating real infrastructure.

Optional OpenAI reasoning can use the same observations, but it does not enable live tools. Evaluations report the execution mode and disclose synthetic data. Unknown and database-saturation cases may require more investigation instead of forcing a rollback or restart.

Consequence: this is a runnable production-oriented foundation, not a certified production incident responder. Every live adapter requires authorization, contract tests, staging failure exercises and operational ownership before activation.
