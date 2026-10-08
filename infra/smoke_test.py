"""Exercise the real HTTP API + durable worker using only demo adapters."""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
import uuid


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()

    def request(path: str, body: dict | None = None, token: str | None = None) -> dict:
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(args.base_url.rstrip("/") + path, data=data, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.load(response)

    def wait_for(incident_id: str, status: str, token: str) -> dict:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            incident = request(f"/api/incidents/{incident_id}", token=token)
            if incident["status"] == status:
                return incident
            if incident["status"] in {"failed", "needs_attention", "rejected"}:
                raise RuntimeError(f"Workflow stopped at {incident['status']}")
            time.sleep(0.5)
        raise TimeoutError(f"Incident did not reach {status}")

    request("/health/ready")
    operator = request("/api/auth/login", {"username": "operator", "password": "sentinel-demo"})["access_token"]
    approver = request("/api/auth/login", {"username": "approver", "password": "sentinel-demo"})["access_token"]
    created = request("/api/incidents", {
        "title": f"Container smoke check {uuid.uuid4().hex[:8]}",
        "service": "checkout-api", "severity": "SEV2",
        "description": "Checkout error rate increased immediately after a deployment.",
        "scenario": "checkout_regression",
    }, operator)
    incident_id = created["id"]
    request(f"/api/incidents/{incident_id}/investigate", {}, operator)
    investigated = wait_for(incident_id, "awaiting_approval", operator)
    assert investigated["hypothesis"]["root_cause"] == "deployment_regression"
    proposal_id = investigated["proposal"]["id"]
    assert investigated["proposal"]["tool"] == "rollback_deployment"
    try:
        request(f"/api/incidents/{incident_id}/approve", {"proposal_id": proposal_id, "reason": "Operator forbidden"}, operator)
    except urllib.error.HTTPError as exc:
        assert exc.code == 403, f"Expected role denial, received {exc.code}"
    else:
        raise AssertionError("An operator was able to approve a high-risk action")
    request(f"/api/incidents/{incident_id}/approve", {"proposal_id": proposal_id, "reason": "Demo smoke verification"}, approver)
    execution = {"proposal_id": proposal_id, "idempotency_key": str(uuid.uuid4())}
    request(f"/api/incidents/{incident_id}/execute", execution, approver)
    resolved = wait_for(incident_id, "resolved", approver)
    assert resolved["report"] and resolved["audit"] and resolved["trace"]
    request(f"/api/incidents/{incident_id}/execute", execution, approver)
    print(f"PASS: API, durable queue, worker, approval role gate and simulated remediation ({incident_id})")


if __name__ == "__main__":
    main()
