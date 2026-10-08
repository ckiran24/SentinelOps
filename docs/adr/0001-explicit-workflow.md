# ADR 0001: use one explicit workflow orchestrator

Status: accepted for the initial implementation.

The incident lifecycle must expose evidence gathering, policy evaluation, waiting for approval and verification. We use LangGraph for the investigation graph and the OpenAI Responses API for optional structured reasoning. We do not introduce multiple overlapping agent orchestrators.

This gives the workflow one state and transition model. Application database jobs own durability at job boundaries. LangGraph persistence is not assumed merely because a graph is used. A future checkpoint design must explicitly address node replay, side effects and approval freshness.

Consequence: adding a second orchestration SDK requires a concrete capability and recovery argument. An experimental managed-agent branch can be compared with the same evals without changing the main safety boundary.
