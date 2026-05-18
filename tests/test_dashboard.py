import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient
import dashboard


def test_root_empty_state(db):
    with patch("dashboard.get_conn", return_value=db):
        client = TestClient(dashboard.app)
        resp = client.get("/")
    assert resp.status_code == 200
    assert "uv run sync.py" in resp.text


def test_root_has_tailwind(db_with_many_activities):
    with patch("dashboard.get_conn", return_value=db_with_many_activities):
        client = TestClient(dashboard.app)
        resp = client.get("/")
    assert resp.status_code == 200
    assert "tailwindcss" in resp.text.lower()


def test_api_data_shape(db_with_many_activities):
    with patch("dashboard.get_conn", return_value=db_with_many_activities):
        client = TestClient(dashboard.app)
        resp = client.get("/api/data?sport_type=GravelRide&weeks=4")
    assert resp.status_code == 200
    data = resp.json()
    assert "week_label" in data
    assert "summary" in data
    assert "activities" in data
    assert "trends" in data


def test_api_summary_fields(db_with_many_activities):
    with patch("dashboard.get_conn", return_value=db_with_many_activities):
        client = TestClient(dashboard.app)
        resp = client.get("/api/data?sport_type=GravelRide&weeks=4")
    s = resp.json()["summary"]
    for field in ("total_km", "avg_speed_kmh", "elevation_m",
                  "km_vs_prev_week_pct", "speed_vs_prev_week_pct",
                  "elevation_vs_prev_week_pct", "sparklines"):
        assert field in s, f"Missing field: {field}"
    for key in ("km", "speed_eas", "elevation"):
        assert key in s["sparklines"]
        assert len(s["sparklines"][key]) == 8


def test_api_activities_have_grade_and_comment(db_with_many_activities):
    with patch("dashboard.get_conn", return_value=db_with_many_activities):
        client = TestClient(dashboard.app)
        resp = client.get("/api/data?sport_type=GravelRide&weeks=12")
    activities = resp.json()["activities"]
    assert len(activities) <= 10
    for act in activities:
        assert "grade" in act
        assert act["grade"] in ("A+", "A", "B+", "B", "C")
        assert "ai_comment" in act  # value may be None


def test_api_trends_length_matches_weeks(db_with_many_activities):
    with patch("dashboard.get_conn", return_value=db_with_many_activities):
        client = TestClient(dashboard.app)
        resp = client.get("/api/data?weeks=12")
    trends = resp.json()["trends"]
    assert len(trends["labels"]) == 12
    assert len(trends["speed_eas"]) == 12
    assert len(trends["volume_km"]) == 12


def test_api_sport_filter(db_with_many_activities):
    with patch("dashboard.get_conn", return_value=db_with_many_activities):
        client = TestClient(dashboard.app)
        resp = client.get("/api/data?sport_type=Run&weeks=4")
    # No Run activities in fixture → activities list empty, summary zeros
    data = resp.json()
    assert data["activities"] == []
