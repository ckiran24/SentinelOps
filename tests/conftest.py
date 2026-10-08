"""Isolate HTTP lifecycle tests from developer databases and external services."""

from __future__ import annotations

import os
import tempfile

import pytest

_test_directory = tempfile.TemporaryDirectory(prefix="sentinelops-tests-")
os.environ["DATABASE_URL"] = f"sqlite:///{_test_directory.name}/test.db"
os.environ["DEMO_MODE"] = "true"
os.environ["JWT_SECRET"] = "test-only-secret-at-least-32-characters-long"
os.environ["LLM_MODE"] = "fixture"
os.environ["LIVE_TOOLS_ENABLED"] = "false"


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from sentinelops.db import Base, engine
    from sentinelops.main import app

    Base.metadata.drop_all(engine)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def credentials(client):
    headers = {}
    for username in ("operator", "approver"):
        response = client.post(
            "/api/auth/login", json={"username": username, "password": "sentinel-demo"}
        )
        assert response.status_code == 200, response.text
        headers[username] = {
            "Authorization": "Bearer " + response.json()["access_token"]
        }
    return headers


def pytest_sessionfinish(session, exitstatus):
    _test_directory.cleanup()
