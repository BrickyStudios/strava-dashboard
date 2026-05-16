# tests/test_db.py
import sqlite3
import pytest
from lib.db import init_db, upsert_activity, get_activities, get_sync_state, set_sync_state


def test_init_db_creates_tables(db):
    cursor = db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row["name"] for row in cursor}
    assert "activities" in tables
    assert "sync_state" in tables


def test_sync_state_enforces_single_row(db):
    set_sync_state(db, epoch=1000, count=5)
    set_sync_state(db, epoch=2000, count=10)
    cursor = db.execute("SELECT COUNT(*) as n FROM sync_state")
    assert cursor.fetchone()["n"] == 1
    state = get_sync_state(db)
    assert state["last_synced_epoch"] == 2000


def test_get_sync_state_returns_none_when_empty(db):
    assert get_sync_state(db) is None


def test_upsert_activity_inserts(db):
    upsert_activity(db, {
        "id": 99, "name": "Test", "sport_type": "GravelRide",
        "start_date": "2026-01-01T10:00:00Z",
        "start_date_local": "2026-01-01T11:00:00",
        "distance": 10000.0, "moving_time": 1800,
        "total_elevation_gain": 50.0, "average_speed": 5.5,
        "max_speed": 10.0, "average_heartrate": None,
        "kilojoules": 300.0,
    })
    cursor = db.execute("SELECT name FROM activities WHERE id = 99")
    assert cursor.fetchone()["name"] == "Test"


def test_upsert_activity_replaces_on_conflict(db):
    act = {
        "id": 99, "name": "Original", "sport_type": "GravelRide",
        "start_date": "2026-01-01T10:00:00Z",
        "start_date_local": "2026-01-01T11:00:00",
        "distance": 10000.0, "moving_time": 1800,
        "total_elevation_gain": 50.0, "average_speed": 5.5,
        "max_speed": 10.0, "average_heartrate": None, "kilojoules": 300.0,
    }
    upsert_activity(db, act)
    act["name"] = "Updated"
    upsert_activity(db, act)
    cursor = db.execute("SELECT name FROM activities WHERE id = 99")
    assert cursor.fetchone()["name"] == "Updated"


def test_get_activities_no_filter(db_with_activities):
    rows = get_activities(db_with_activities)
    assert len(rows) == 3


def test_get_activities_filter_by_sport(db_with_activities):
    rows = get_activities(db_with_activities, sport_type="GravelRide")
    assert len(rows) == 2
    assert all(r["sport_type"] == "GravelRide" for r in rows)


def test_get_activities_filter_by_date(db_with_activities):
    rows = get_activities(
        db_with_activities,
        since_local="2026-05-15",
        until_local="2026-05-17",
    )
    assert len(rows) == 1
    assert rows[0]["id"] == 2
