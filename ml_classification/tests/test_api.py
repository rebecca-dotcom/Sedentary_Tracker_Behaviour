"""Tests for the FastAPI analytics service endpoints that need no database."""

from fastapi.testclient import TestClient

from api import app

client = TestClient(app)


def test_health_endpoint_reports_loinc_code():
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"
    assert body["loinc_code"] == "87705-0"


def test_daily_summary_has_expected_schema():
    response = client.get("/api/analytics/daily")
    assert response.status_code == 200
    body = response.json()
    assert body["period_type"] == "daily"
    assert 0 <= body["activity_score"] <= 100


def test_unknown_route_returns_404():
    assert client.get("/api/does-not-exist").status_code == 404
