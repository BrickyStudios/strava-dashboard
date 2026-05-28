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
    for col in ("ai_comment TEXT", "detail_comment TEXT", "surface_json TEXT"):
        try:
            conn.execute(f"ALTER TABLE activities ADD COLUMN {col}")
            conn.commit()
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e):
                raise
    conn.execute("""
        CREATE TABLE IF NOT EXISTS segment_efforts (
            segment_id         INTEGER NOT NULL,
            segment_name       TEXT,
            segment_distance_m REAL,
            activity_id        INTEGER NOT NULL,
            elapsed_time_s     INTEGER,
            start_date_local   TEXT,
            pr_rank            INTEGER,
            overall_rank       INTEGER,
            PRIMARY KEY (segment_id, activity_id)
        )
    """)
    conn.commit()


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


def upsert_segment_efforts(conn: sqlite3.Connection, activity_id: int, efforts: list[dict]) -> None:
    if not efforts:
        conn.execute(
            """INSERT OR IGNORE INTO segment_efforts
               (segment_id, segment_name, segment_distance_m, activity_id,
                elapsed_time_s, start_date_local, pr_rank, overall_rank)
               VALUES (0, NULL, NULL, ?, NULL, NULL, NULL, NULL)""",
            (activity_id,),
        )
    else:
        for e in efforts:
            conn.execute(
                """INSERT OR REPLACE INTO segment_efforts
                   (segment_id, segment_name, segment_distance_m, activity_id,
                    elapsed_time_s, start_date_local, pr_rank, overall_rank)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (e["segment_id"], e["segment_name"], e["segment_distance_m"],
                 activity_id, e["elapsed_time_s"], e["start_date_local"],
                 e.get("pr_rank"), e.get("overall_rank")),
            )
    conn.commit()


def get_my_records(conn: sqlite3.Connection) -> list:
    """Segments where user holds overall rank 1 (KOM) or personal record (pr_rank=1)."""
    return conn.execute("""
        SELECT segment_id, segment_name, segment_distance_m,
               MIN(elapsed_time_s) AS elapsed_time_s,
               MAX(start_date_local) AS activity_date,
               MIN(overall_rank) AS overall_rank
        FROM segment_efforts
        WHERE (overall_rank = 1 OR pr_rank = 1) AND segment_id != 0
        GROUP BY segment_id
        ORDER BY overall_rank ASC NULLS LAST, segment_distance_m DESC
    """).fetchall()


def get_all_ranked_efforts(conn: sqlite3.Connection) -> list:
    return conn.execute("""
        SELECT segment_id, segment_name, segment_distance_m,
               elapsed_time_s, overall_rank, pr_rank, start_date_local
        FROM segment_efforts
        WHERE segment_id != 0
        ORDER BY segment_id, start_date_local ASC
    """).fetchall()
