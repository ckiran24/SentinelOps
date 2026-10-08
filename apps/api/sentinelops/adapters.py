"""Typed tool adapters. Fixture mutations never contact infrastructure."""

from copy import deepcopy

from .policy import HIGH_RISK_TOOLS, PolicyViolation, validate_tool
from .runbooks import retrieve_runbooks

FIXTURES = {
    "checkout_regression": {
        "metrics": {
            "error_rate": 0.14,
            "latency_p95_ms": 1850,
            "cpu_percent": 48,
            "memory_percent": 55,
            "memory_trend_mb_per_hour": 8,
            "database_connections": 32,
            "connection_limit": 100,
        },
        "deployments": [
            {
                "id": "checkout-v47",
                "version": "v47",
                "minutes_ago": 8,
                "error_rate_before": 0.004,
                "error_rate_after": 0.14,
            }
        ],
        "logs": ["Checkout handler validation exceptions increased after release v47."],
        "database": {"active_connections": 32, "connection_limit": 100, "slow_queries": []},
    },
    "database_saturation": {
        "metrics": {
            "error_rate": 0.09,
            "latency_p95_ms": 4200,
            "cpu_percent": 67,
            "memory_percent": 52,
            "memory_trend_mb_per_hour": 5,
            "database_connections": 97,
            "connection_limit": 100,
        },
        "deployments": [],
        "logs": ["Connection pool exhausted; request waiting for database."],
        "database": {
            "active_connections": 97,
            "connection_limit": 100,
            "slow_queries": [{"name": "checkout_read", "duration_ms": 2800}],
        },
    },
    "memory_leak": {
        "metrics": {
            "error_rate": 0.08,
            "latency_p95_ms": 2100,
            "cpu_percent": 78,
            "memory_percent": 96,
            "memory_trend_mb_per_hour": 320,
            "database_connections": 26,
            "connection_limit": 100,
        },
        "deployments": [],
        "logs": ["Heap grows between requests; repeated OOM pressure warnings."],
        "database": {"active_connections": 26, "connection_limit": 100, "slow_queries": []},
    },
    "unknown": {
        "metrics": {
            "error_rate": 0.013,
            "latency_p95_ms": 350,
            "cpu_percent": 42,
            "memory_percent": 51,
            "memory_trend_mb_per_hour": 3,
            "database_connections": 22,
            "connection_limit": 100,
        },
        "deployments": [],
        "logs": ["Intermittent symptom; no corroborating causal signal."],
        "database": {"active_connections": 22, "connection_limit": 100, "slow_queries": []},
    },
}


class FixtureAdapter:
    """Caller-controlled synthetic telemetry for local demos and evaluator fixtures."""

    def __init__(self, scenario="unknown", observations=None):
        self.observations = deepcopy(FIXTURES.get(scenario, FIXTURES["unknown"]))
        if observations is not None:
            self.observations.update(deepcopy(observations))
        self.calls: list[dict] = []

    def read(self, tool: str, args: dict):
        typed = validate_tool(tool, args)
        if tool in HIGH_RISK_TOOLS:
            raise PolicyViolation("Mutation requires the separately authorized worker path")
        self.calls.append({"tool": tool, "args": typed})
        if tool == "get_recent_deployments":
            return deepcopy(self.observations.get("deployments", []))[: typed["limit"]]
        if tool == "query_service_metrics":
            return deepcopy(self.observations.get("metrics", {}))
        if tool == "search_logs":
            # Query is a bounded search hint, never an instruction to execute.
            return deepcopy(self.observations.get("logs", []))[: typed["limit"]]
        if tool == "query_database":
            database = deepcopy(self.observations.get("database", {}))
            if typed["template"] == "connection_stats":
                return {
                    key: database.get(key) for key in ("active_connections", "connection_limit")
                }
            return database.get("slow_queries", [])
        if tool == "get_runbook":
            return retrieve_runbooks(typed["query"], typed["service"])
        raise PolicyViolation("No read adapter for this tool")

    def simulate_mutation(self, tool: str, args: dict) -> dict:
        typed = validate_tool(tool, args)
        if tool not in HIGH_RISK_TOOLS:
            raise PolicyViolation("Only allowlisted remediation can be simulated")
        # Simulated post-action telemetry is derived from explicit fixture state.
        if tool == "rollback_deployment":
            deployments = self.observations.get("deployments", [])
            matching = next(
                (item for item in deployments if item.get("id") == typed["deployment_id"]), None
            )
            if matching is None:
                raise PolicyViolation("Deployment does not belong to the observed fixture")
            self.observations["metrics"]["error_rate"] = matching.get("error_rate_before", 0.004)
            self.observations["metrics"]["latency_p95_ms"] = 280
        else:
            self.observations["metrics"]["memory_percent"] = 38
            self.observations["metrics"]["memory_trend_mb_per_hour"] = 0
            self.observations["metrics"]["error_rate"] = 0.004
        return {
            "simulated": True,
            "tool": tool,
            "args": typed,
            "metrics_after": deepcopy(self.observations["metrics"]),
        }


class LiveAdapter:
    """No placeholder can silently mutate or masquerade as live infrastructure."""

    def read(self, tool: str, args: dict):
        validate_tool(tool, args)
        raise PolicyViolation("Live infrastructure adapters are not configured; operation blocked")

    def simulate_mutation(self, tool: str, args: dict):
        validate_tool(tool, args)
        raise PolicyViolation("Live remediation adapters are not configured; operation blocked")
