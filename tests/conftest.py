# tests/conftest.py
import sqlite3
import pytest
from lib.db import init_db, upsert_activity

@pytest.fixture
def db():
    """In-memory SQLite DB, fully initialized."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    yield conn
    conn.close()

@pytest.fixture
def db_with_activities(db):
    """DB with 3 sample activities across 2 ISO weeks."""
    activities = [
        {
            "id": 1,
            "name": "Morning Ride",
            "sport_type": "GravelRide",
            # W19 (May 5 is W19, separating from W20 activities below)
            "start_date": "2026-05-05T07:00:00Z",
            "start_date_local": "2026-05-05T09:00:00",
            "distance": 48900.0,
            "moving_time": 8928,
            "total_elevation_gain": 213.0,
            "average_speed": 5.476,
            "max_speed": 18.0,
            "average_heartrate": None,
            "kilojoules": 800.0,
        },
        {
            "id": 2,
            "name": "Seen Runde",
            "sport_type": "GravelRide",
            "start_date": "2026-05-16T07:00:00Z",
            "start_date_local": "2026-05-16T09:00:00",
            "distance": 75300.0,
            "moving_time": 11580,
            "total_elevation_gain": 417.0,
            "average_speed": 6.503,
            "max_speed": 18.025,
            "average_heartrate": None,
            "kilojoules": 1376.0,
        },
        {
            "id": 3,
            "name": "Short Run",
            "sport_type": "Run",
            "start_date": "2026-05-14T06:00:00Z",
            "start_date_local": "2026-05-14T08:00:00",
            "distance": 5000.0,
            "moving_time": 1500,
            "total_elevation_gain": 20.0,
            "average_speed": 3.333,
            "max_speed": 4.5,
            "average_heartrate": 155.0,
            "kilojoules": 200.0,
        },
    ]
    for a in activities:
        upsert_activity(db, a)
    return db
