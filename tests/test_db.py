# tests/test_db.py
import sqlite3
import pytest
from lib.db import init_db, upsert_activity, get_activities, get_sync_state, set_sync_state


def _sample_activity(act_id: int, date_str: str = "2026-05-01") -> dict:
    return {
        "id": act_id, "name": f"Ride {act_id}", "sport_type": "GravelRide",
        "start_date": f"{date_str}T10:00:00Z",
        "start_date_local": f"{date_str}T12:00:00",
        "distance": 40000.0, "moving_time": 6000,
        "total_elevation_gain": 200.0, "average_speed": 6.5,
        "max_speed": 15.0, "average_heartrate": None, "kilojoules": 700.0,
    }


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


def test_init_db_adds_ai_comment_column(db):
    cols = [row[1] for row in db.execute("PRAGMA table_info(activities)").fetchall()]
    assert "ai_comment" in cols


def test_init_db_is_idempotent_with_existing_ai_comment(db):
    from lib.db import init_db
    init_db(db)  # second call — should not crash


def test_migration_adds_ai_comment_to_old_schema(db):
    # Simulate a DB that existed before the ai_comment column was added
    db.execute("DROP TABLE activities")
    db.execute("""
        CREATE TABLE activities (
            id INTEGER PRIMARY KEY, name TEXT, sport_type TEXT,
            start_date_utc TEXT, start_date_local TEXT,
            distance_m REAL, moving_time_s INTEGER,
            elevation_gain_m REAL, avg_speed_ms REAL,
            max_speed_ms REAL, avg_heartrate REAL,
            kilojoules REAL, raw_json TEXT
        )
    """)
    db.commit()
    from lib.db import init_db
    init_db(db)
    cols = [row[1] for row in db.execute("PRAGMA table_info(activities)").fetchall()]
    assert "ai_comment" in cols


def test_ai_comment_round_trip(db):
    from lib.db import upsert_activity
    upsert_activity(db, {
        "id": 99, "name": "Test", "sport_type": "GravelRide",
        "start_date": "2026-05-01T10:00:00Z",
        "start_date_local": "2026-05-01T12:00:00",
        "distance": 40000.0, "moving_time": 6000,
        "total_elevation_gain": 200.0, "average_speed": 6.5,
        "max_speed": 15.0, "average_heartrate": None, "kilojoules": 700.0,
    })
    db.execute("UPDATE activities SET ai_comment = 'Gute Fahrt!' WHERE id = 99")
    db.commit()
    row = db.execute("SELECT ai_comment FROM activities WHERE id = 99").fetchone()
    assert row[0] == "Gute Fahrt!"


# --- segment_efforts tests ---

def test_init_db_creates_segment_efforts_table(db):
    cursor = db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row["name"] for row in cursor}
    assert "segment_efforts" in tables


def test_upsert_segment_efforts_inserts(db):
    from lib.db import upsert_segment_efforts
    upsert_activity(db, _sample_activity(1))
    upsert_segment_efforts(db, activity_id=1, efforts=[{
        "segment_id": 100, "segment_name": "Berg Sprint",
        "segment_distance_m": 420.0, "elapsed_time_s": 54,
        "start_date_local": "2026-05-01T12:30:00",
        "pr_rank": 1, "overall_rank": 1,
    }])
    row = db.execute(
        "SELECT * FROM segment_efforts WHERE segment_id=100 AND activity_id=1"
    ).fetchone()
    assert row is not None
    assert row["segment_name"] == "Berg Sprint"
    assert row["overall_rank"] == 1


def test_upsert_segment_efforts_sentinel_for_empty(db):
    from lib.db import upsert_segment_efforts
    upsert_activity(db, _sample_activity(2, "2026-05-02"))
    upsert_segment_efforts(db, activity_id=2, efforts=[])
    row = db.execute(
        "SELECT * FROM segment_efforts WHERE activity_id=2 AND segment_id=0"
    ).fetchone()
    assert row is not None


def test_get_koms_returns_rank1_segments(db):
    from lib.db import upsert_segment_efforts, get_koms
    upsert_activity(db, _sample_activity(1))
    upsert_segment_efforts(db, activity_id=1, efforts=[
        {"segment_id": 100, "segment_name": "KOM Seg", "segment_distance_m": 500.0,
         "elapsed_time_s": 60, "start_date_local": "2026-05-01T12:30:00",
         "pr_rank": 1, "overall_rank": 1},
        {"segment_id": 200, "segment_name": "2nd Seg", "segment_distance_m": 300.0,
         "elapsed_time_s": 45, "start_date_local": "2026-05-01T12:35:00",
         "pr_rank": 1, "overall_rank": 2},
    ])
    koms = get_koms(db)
    assert len(koms) == 1
    assert koms[0]["segment_id"] == 100


def test_get_all_ranked_efforts_excludes_sentinel(db):
    from lib.db import upsert_segment_efforts, get_all_ranked_efforts
    upsert_activity(db, _sample_activity(2, "2026-05-02"))
    upsert_segment_efforts(db, activity_id=2, efforts=[])
    efforts = get_all_ranked_efforts(db)
    assert all(e["segment_id"] != 0 for e in efforts)
