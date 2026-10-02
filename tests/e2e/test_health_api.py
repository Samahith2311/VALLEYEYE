from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from valleyeye.api.app import create_app
from valleyeye.core.settings import Settings


@pytest.mark.e2e
def test_health_and_ready_endpoints() -> None:
    client = TestClient(create_app(Settings()))

    health = client.get("/health")
    ready = client.get("/ready")

    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert ready.status_code == 200
    assert ready.json()["checks"]["settings"] == "ok"


@pytest.mark.e2e
def test_unknown_route_uses_structured_error() -> None:
    client = TestClient(create_app(Settings()))

    response = client.get("/missing-route")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "JOB_NOT_FOUND"
    assert response.json()["error"]["request_id"]
