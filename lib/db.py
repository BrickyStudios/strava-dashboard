# lib/db.py
import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "activities.db"


def get_conn(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS activities (
            id                INTEGER PRIMARY KEY,
            name              TEXT,
            sport_type        TEXT,
            start_date_utc    TEXT,
            start_date_local  TEXT,
            distance_m        REAL,
            moving_time_s     INTEGER,
            elevation_gain_m  REAL,
            avg_speed_ms      REAL,
            max_speed_ms      REAL,
            avg_heartrate     REAL,
            kilojoules        REAL,
            ai_comment        TEXT,
            raw_json          TEXT
        );

        CREATE TABLE IF NOT EXISTS sync_state (
            id                INTEGER PRIMARY KEY CHECK (id = 1),
            last_synced_epoch INTEGER,
            total_activities  INTEGER
        );
    """)
    conn.commit()  # explicit commit after DDL
    # Safe migration for DBs created before ai_comment column existed
    try:
        conn.execute("ALTER TABLE activities ADD COLUMN ai_comment TEXT")
        conn.commit()
    except sqlite3.OperationalError as e:
        if "duplicate column name" not in str(e):
            raise


def upsert_activity(conn: sqlite3.Connection, activity: dict) -> None:
    conn.execute("""
        INSERT OR REPLACE INTO activities (
            id, name, sport_type, start_date_utc, start_date_local,
            distance_m, moving_time_s, elevation_gain_m,
            avg_speed_ms, max_speed_ms, avg_heartrate, kilojoules, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        activity["id"],
        activity["name"],
        activity.get("sport_type", activity.get("type")),
        activity.get("start_date"),
        activity.get("start_date_local"),
        activity.get("distance"),
        activity.get("moving_time"),
        activity.get("total_elevation_gain"),
        activity.get("average_speed"),
        activity.get("max_speed"),
        activity.get("average_heartrate"),
        activity.get("kilojoules"),
        json.dumps(activity),
    ))
    conn.commit()


def get_activities(
    conn: sqlite3.Connection,
    sport_type: str | None = None,
    since_local: str | None = None,
    until_local: str | None = None,
) -> list[sqlite3.Row]:
    query = "SELECT * FROM activities WHERE 1=1"
    params: list = []
    if sport_type:
        query += " AND sport_type = ?"
        params.append(sport_type)
    if since_local:
        query += " AND start_date_local >= ?"
        params.append(since_local)
    if until_local:
        query += " AND start_date_local <= ?"
        params.append(until_local + "T23:59:59")
    query += " ORDER BY start_date_local ASC"
    return conn.execute(query, params).fetchall()


def get_sync_state(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute("SELECT * FROM sync_state WHERE id = 1").fetchone()
    return dict(row) if row else None


def set_sync_state(conn: sqlite3.Connection, epoch: int, count: int) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO sync_state (id, last_synced_epoch, total_activities) VALUES (1, ?, ?)",
        (epoch, count),
    )
    conn.commit()
