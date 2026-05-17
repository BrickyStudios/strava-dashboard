import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient


def test_root_empty_state(db):
    with patch("dashboard.get_conn", return_value=db):
        from dashboard import app
        client = TestClient(app)
        resp = client.get("/")
    assert resp.status_code == 200
    assert "uv run sync.py" in resp.text
    assert "chart.js" not in resp.text.lower()


def test_root_shows_charts_when_data_exists(db_with_activities):
    with patch("dashboard.get_conn", return_value=db_with_activities):
        from dashboard import app
        client = TestClient(app)
        resp = client.get("/")
    assert resp.status_code == 200
    assert "chart.js" in resp.text.lower()


def test_api_data_returns_json(db_with_activities):
    with patch("dashboard.get_conn", return_value=db_with_activities):
        from dashboard import app
        client = TestClient(app)
        resp = client.get("/api/data?sport_type=GravelRide&weeks=4")
    assert resp.status_code == 200
    data = resp.json()
    assert "labels" in data
    assert "speed" in data
    assert "volume_km" in data
    assert "eas" in data
    assert len(data["labels"]) == 4


def test_api_data_speed_is_none_for_empty_weeks(db_with_activities):
    with patch("dashboard.get_conn", return_value=db_with_activities):
        from dashboard import app
        client = TestClient(app)
        resp = client.get("/api/data?sport_type=GravelRide&weeks=52")
    data = resp.json()
    null_count = sum(1 for v in data["speed"] if v is None)
    assert null_count > 40


def test_api_data_all_sports(db_with_activities):
    with patch("dashboard.get_conn", return_value=db_with_activities):
        from dashboard import app
        client = TestClient(app)
        resp = client.get("/api/data?weeks=4")
    assert resp.status_code == 200
