# Strava Analytics & Performance Tracking — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the existing `strava-mcp` project with a SQLite cache layer, sync script, three MCP analytics tools, and a local FastAPI dashboard to track cycling performance over time.

**Architecture:** All components share one SQLite database at `data/activities.db`. `sync.py` populates it from the Strava API. `main.py` grows three read-only analytics tools that query it. `dashboard.py` serves a Chart.js web UI at localhost:8080.

**Tech Stack:** Python 3.12+, sqlite3 (stdlib), httpx, FastAPI, uvicorn, Chart.js (CDN), python-dotenv, pytest

**Spec:** `docs/superpowers/specs/2026-05-16-strava-analytics-design.md`

---

## File Map

| File | Status | Responsibility |
|---|---|---|
| `lib/db.py` | NEW | SQLite init, upsert, query, sync state |
| `sync.py` | NEW | Token refresh + write-back, paginated Strava fetch, rate limit handling |
| `main.py` | MODIFY | Add 3 analytics MCP tools |
| `dashboard.py` | NEW | FastAPI + Chart.js dashboard, `/api/data` endpoint |
| `pyproject.toml` | MODIFY | Add fastapi, uvicorn, pytest |
| `.gitignore` | MODIFY | Add `data/` |
| `tests/conftest.py` | NEW | Shared pytest fixtures (in-memory DB) |
| `tests/test_db.py` | NEW | Unit tests for lib/db.py |
| `tests/test_sync.py` | NEW | Unit tests for sync.py (mocked httpx) |
| `tests/test_analytics.py` | NEW | Unit tests for 3 MCP analytics tools |
| `tests/test_dashboard.py` | NEW | FastAPI TestClient tests |

---

## Task 1: Project Setup

**Files:**
- Modify: `pyproject.toml`
- Modify: `.gitignore`

- [ ] **Step 1: Add dependencies to pyproject.toml**

Replace the `dependencies` block:

```toml
dependencies = [
    "fastapi>=0.115.0",
    "httpx>=0.28.1",
    "mcp[cli]>=1.6.0",
    "meteostat>=1.6.8",
    "pre-commit>=4.2.0",
    "python-dotenv>=1.1.0",
    "uvicorn[standard]>=0.34.0",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 2: Add `data/` to .gitignore**

Create or append to `.gitignore`:
```
data/
__pycache__/
*.pyc
.env
```

- [ ] **Step 3: Create directories and install**

```bash
mkdir -p data tests
touch data/.gitkeep
cd /home/user/Dokumente/strava-mcp && uv sync
```

Expected: uv resolves and installs fastapi + uvicorn without errors.

- [ ] **Step 4: Commit**

```bash
git -C /home/user/Dokumente/strava-mcp add pyproject.toml .gitignore data/.gitkeep
git -C /home/user/Dokumente/strava-mcp commit -m "chore: add fastapi, uvicorn, pytest deps; gitignore data/"
```

---

## Task 2: Database Layer (`lib/db.py`)

**Files:**
- Create: `lib/db.py`
- Create: `tests/conftest.py`
- Create: `tests/test_db.py`

- [ ] **Step 1: Write conftest.py with in-memory DB fixture**

```python
# tests/conftest.py
import sqlite3
import pytest
from lib.db import init_db, upsert_activity

@pytest.fixture
def db():
    """In-memory SQLite DB, fully initialized."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    yield conn
    conn.close()

@pytest.fixture
def db_with_activities(db):
    """DB with 3 sample activities across 2 weeks."""
    activities = [
        {
            "id": 1,
            "name": "Morning Ride",
            "sport_type": "GravelRide",
            # W19 (May 11 is W20 — use May 5 to land in W19 and separate weeks)
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
```

- [ ] **Step 2: Write failing tests**

```python
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
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd /home/user/Dokumente/strava-mcp && uv run pytest tests/test_db.py -v
```

Expected: `ModuleNotFoundError: No module named 'lib.db'`

- [ ] **Step 4: Implement `lib/db.py`**

```python
# lib/db.py
import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "activities.db"


def get_conn(path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
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
            raw_json          TEXT
        );

        CREATE TABLE IF NOT EXISTS sync_state (
            id                INTEGER PRIMARY KEY CHECK (id = 1),
            last_synced_epoch INTEGER,
            total_activities  INTEGER
        );
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
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /home/user/Dokumente/strava-mcp && uv run pytest tests/test_db.py -v
```

Expected: all 7 tests PASS.

- [ ] **Step 6: Commit**

```bash
git -C /home/user/Dokumente/strava-mcp add lib/db.py tests/conftest.py tests/test_db.py
git -C /home/user/Dokumente/strava-mcp commit -m "feat: add SQLite database layer (lib/db.py)"
```

---

## Task 3: Sync Script (`sync.py`)

**Files:**
- Create: `sync.py`
- Create: `tests/test_sync.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_sync.py
import time
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path


def make_response(activities, headers=None):
    mock = MagicMock()
    mock.json.return_value = activities
    mock.headers = headers or {"X-RateLimit-Limit": "100,1000", "X-RateLimit-Usage": "1,1"}
    mock.raise_for_status.return_value = None
    return mock


def test_refresh_tokens_writes_back_to_env(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "STRAVA_CLIENT_ID=123\n"
        "STRAVA_CLIENT_SECRET=secret\n"
        "STRAVA_ACCESS_TOKEN=old_access\n"
        "STRAVA_REFRESH_TOKEN=old_refresh\n"
    )

    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "access_token": "new_access",
        "refresh_token": "new_refresh",
    }
    mock_resp.raise_for_status.return_value = None

    with patch("httpx.post", return_value=mock_resp):
        from sync import refresh_tokens
        token = refresh_tokens(env_path=env_file)

    assert token == "new_access"
    content = env_file.read_text()
    assert "STRAVA_ACCESS_TOKEN=new_access" in content
    assert "STRAVA_REFRESH_TOKEN=new_refresh" in content


def test_check_rate_limit_sleeps_when_approaching_15min_limit():
    headers = {"X-RateLimit-Limit": "100,1000", "X-RateLimit-Usage": "91,200"}
    with patch("time.sleep") as mock_sleep:
        from sync import check_rate_limit
        check_rate_limit(headers)
        mock_sleep.assert_called_once()
        sleep_seconds = mock_sleep.call_args[0][0]
        assert sleep_seconds > 0


def test_check_rate_limit_raises_on_daily_limit():
    headers = {"X-RateLimit-Limit": "100,1000", "X-RateLimit-Usage": "5,995"}
    from sync import DailyLimitReached, check_rate_limit
    with pytest.raises(DailyLimitReached):
        check_rate_limit(headers)


def test_check_rate_limit_passes_when_under_limit():
    headers = {"X-RateLimit-Limit": "100,1000", "X-RateLimit-Usage": "10,100"}
    from sync import check_rate_limit
    check_rate_limit(headers)  # should not raise or sleep


def test_sync_upserts_activities_into_db(db):
    sample = [
        {
            "id": 42,
            "name": "Test Ride",
            "sport_type": "GravelRide",
            "type": "GravelRide",
            "start_date": "2026-05-01T08:00:00Z",
            "start_date_local": "2026-05-01T10:00:00",
            "distance": 50000.0,
            "moving_time": 7200,
            "total_elevation_gain": 300.0,
            "average_speed": 6.944,
            "max_speed": 15.0,
            "average_heartrate": None,
            "kilojoules": 900.0,
        }
    ]

    page1 = make_response(sample)
    page2 = make_response([])

    with patch("httpx.get", side_effect=[page1, page2]):
        with patch("sync.refresh_tokens", return_value="fake_token"):
            from sync import run_sync
            run_sync(conn=db, full=False, last_epoch=0)

    from lib.db import get_activities
    rows = get_activities(db)
    assert len(rows) == 1
    assert rows[0]["id"] == 42
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/user/Dokumente/strava-mcp && uv run pytest tests/test_sync.py -v
```

Expected: `ModuleNotFoundError: No module named 'sync'`

- [ ] **Step 3: Implement `sync.py`**

```python
# sync.py
"""Sync Strava activities into local SQLite database."""

import time
import httpx
import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from dotenv import dotenv_values, set_key

from lib.db import get_conn, init_db, upsert_activity, get_sync_state, set_sync_state

ENV_PATH = Path(__file__).parent / ".env"
STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"
STRAVA_ACTIVITIES_URL = "https://www.strava.com/api/v3/athlete/activities"


class DailyLimitReached(Exception):
    pass


def refresh_tokens(env_path: Path = ENV_PATH) -> str:
    envs = dotenv_values(env_path)
    resp = httpx.post(STRAVA_TOKEN_URL, data={
        "client_id": envs["STRAVA_CLIENT_ID"],
        "client_secret": envs["STRAVA_CLIENT_SECRET"],
        "refresh_token": envs["STRAVA_REFRESH_TOKEN"],
        "grant_type": "refresh_token",
    })
    resp.raise_for_status()
    data = resp.json()
    set_key(str(env_path), "STRAVA_ACCESS_TOKEN", data["access_token"])
    set_key(str(env_path), "STRAVA_REFRESH_TOKEN", data["refresh_token"])
    return data["access_token"]


def check_rate_limit(headers: dict) -> None:
    usage_raw = headers.get("X-RateLimit-Usage", "0,0")
    limit_raw = headers.get("X-RateLimit-Limit", "100,1000")
    fifteen_min, daily = (int(x) for x in usage_raw.split(","))
    _, daily_limit = (int(x) for x in limit_raw.split(","))

    if daily >= daily_limit - 10:
        raise DailyLimitReached(f"Daily limit reached: {daily}/{daily_limit}")

    if fifteen_min >= 90:
        now = datetime.now()
        current_quarter = (now.minute // 15)
        next_quarter_minute = (current_quarter + 1) * 15
        if next_quarter_minute >= 60:
            sleep_s = (60 - now.minute) * 60 - now.second + 5
        else:
            sleep_s = (next_quarter_minute - now.minute) * 60 - now.second + 5
        print(f"Rate limit at {fifteen_min}/100 — sleeping {sleep_s}s until next window...")
        time.sleep(sleep_s)


def run_sync(conn: sqlite3.Connection, full: bool, last_epoch: int, access_token: str = "fake") -> int:
    after = 0 if full else (last_epoch or 0)
    page = 1
    total = 0

    while True:
        resp = httpx.get(
            STRAVA_ACTIVITIES_URL,
            params={"per_page": 200, "page": page, "after": after},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        check_rate_limit(resp.headers)

        activities = resp.json()
        if not activities:
            break

        for activity in activities:
            upsert_activity(conn, activity)

        total += len(activities)
        print(f"Page {page}: synced {len(activities)} activities ({total} total)")
        page += 1

    return total


def main():
    parser = argparse.ArgumentParser(description="Sync Strava activities to SQLite")
    parser.add_argument("--full", action="store_true", help="Re-fetch all activities")
    args = parser.parse_args()

    conn = get_conn()
    init_db(conn)

    print("Refreshing Strava tokens...")
    access_token = refresh_tokens()

    state = get_sync_state(conn)
    last_epoch = 0 if args.full else (state["last_synced_epoch"] if state else 0)

    if args.full:
        print("Full sync — fetching all activities...")
    else:
        print(f"Incremental sync from epoch {last_epoch}...")

    try:
        synced = run_sync(conn, full=args.full, last_epoch=last_epoch, access_token=access_token)
    except DailyLimitReached as e:
        print(f"Warning: {e}. Run again tomorrow to continue.")
        return

    total_count = conn.execute("SELECT COUNT(*) FROM activities").fetchone()[0]
    set_sync_state(conn, epoch=int(datetime.now(timezone.utc).timestamp()), count=total_count)
    print(f"Sync complete. {synced} new activities. {total_count} total in DB.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/user/Dokumente/strava-mcp && uv run pytest tests/test_sync.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 5: Run a real sync to populate the DB**

```bash
cd /home/user/Dokumente/strava-mcp && uv run sync.py --full
```

Expected: prints pages synced, ends with "Sync complete. N total in DB."

- [ ] **Step 6: Commit**

```bash
git -C /home/user/Dokumente/strava-mcp add sync.py tests/test_sync.py
git -C /home/user/Dokumente/strava-mcp commit -m "feat: add Strava sync script with token refresh and rate limit handling"
```

---

## Task 4: MCP Analytics Tools (`main.py`)

**Files:**
- Modify: `main.py`
- Create: `tests/test_analytics.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_analytics.py
"""Tests for the 3 analytics MCP tools. Uses in-memory DB from conftest."""
import pytest
from unittest.mock import patch


def test_get_performance_trend_returns_table(db_with_activities):
    with patch("main.get_conn", return_value=db_with_activities):
        from main import get_performance_trend
        result = get_performance_trend(sport_type="GravelRide", metric="avg_speed_kmh", weeks=4)
    assert "Week" in result
    assert "km/h" in result
    # Should contain at least one real value row
    assert "2026-W" in result


def test_get_performance_trend_speed_is_distance_weighted(db_with_activities):
    with patch("main.get_conn", return_value=db_with_activities):
        from main import get_performance_trend
        result = get_performance_trend(sport_type="GravelRide", metric="avg_speed_kmh", weeks=4)
    # Week W20 has the Seen Runde (75.3km, 3:13h → 23.4 km/h)
    # Simple arithmetic mean would differ from distance-weighted if multiple rides exist
    assert "23." in result or "19." in result  # real speed values present


def test_get_performance_trend_includes_empty_weeks(db_with_activities):
    with patch("main.get_conn", return_value=db_with_activities):
        from main import get_performance_trend
        result = get_performance_trend(sport_type="GravelRide", metric="avg_speed_kmh", weeks=12)
    assert "—" in result  # empty weeks shown


def test_get_weekly_volume_returns_table(db_with_activities):
    with patch("main.get_conn", return_value=db_with_activities):
        from main import get_weekly_volume
        result = get_weekly_volume(sport_type="GravelRide", weeks=4)
    assert "km" in result
    assert "Elevation" in result
    assert "2026-W" in result


def test_get_weekly_volume_all_sports(db_with_activities):
    with patch("main.get_conn", return_value=db_with_activities):
        from main import get_weekly_volume
        result = get_weekly_volume(sport_type=None, weeks=4)
    # Run is 5km, should appear in total
    assert "5." in result or "80." in result


def test_get_personal_bests_returns_all_fields(db_with_activities):
    with patch("main.get_conn", return_value=db_with_activities):
        from main import get_personal_bests
        result = get_personal_bests(sport_type="GravelRide")
    assert "longest_distance" in result
    assert "longest_duration" in result
    assert "fastest" in result
    assert "elevation" in result
    # Seen Runde is the longest at 75.3km
    assert "75." in result


def test_get_personal_bests_sport_filter(db_with_activities):
    with patch("main.get_conn", return_value=db_with_activities):
        from main import get_personal_bests
        result = get_personal_bests(sport_type="Run")
    assert "5." in result  # 5km run
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/user/Dokumente/strava-mcp && uv run pytest tests/test_analytics.py -v
```

Expected: `ImportError` — tools don't exist yet.

- [ ] **Step 3: Add analytics tools to `main.py`**

Add these imports at the top of `main.py`:

```python
from datetime import date, timedelta
from typing import Literal
from lib.db import get_conn, init_db, get_activities as get_db_activities
```

Note: `lib.db.get_activities` is aliased to avoid shadowing the existing `from lib.api import get_activities` (the async API version used by `fetch_activities`). All three analytics tools call `get_db_activities(...)`, not `get_activities(...)`.

Add these three tools below the existing tools:

```python
def _iso_week_label(d: date) -> str:
    iso = d.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _week_start(d: date) -> date:
    """Monday of the ISO week containing d."""
    return d - timedelta(days=d.weekday())


def _trailing_weeks(n: int) -> list[str]:
    today = date.today()
    monday = _week_start(today)
    return [_iso_week_label(monday - timedelta(weeks=i)) for i in range(n - 1, -1, -1)]


@mcp.tool()
def get_performance_trend(
    sport_type: str | None,
    metric: Literal["avg_speed_kmh", "distance_km", "elevation_m"],
    weeks: int = 12,
) -> str:
    """Weekly performance trend from local DB.

    Args:
        sport_type: Strava sport type (e.g. 'GravelRide') or None for all.
        metric: 'avg_speed_kmh', 'distance_km', or 'elevation_m'.
        weeks: number of trailing ISO weeks to show.

    Returns:
        Formatted table of per-week aggregates.
    """
    conn = get_conn()
    init_db(conn)
    rows = get_db_activities(conn, sport_type=sport_type)

    # Bucket by ISO week label derived from start_date_local
    buckets: dict[str, dict] = {}
    for row in rows:
        local_date = date.fromisoformat(row["start_date_local"][:10])
        label = _iso_week_label(local_date)
        if label not in buckets:
            buckets[label] = {"dist": 0.0, "time": 0, "elev": 0.0}
        buckets[label]["dist"] += row["distance_m"] or 0
        buckets[label]["time"] += row["moving_time_s"] or 0
        buckets[label]["elev"] += row["elevation_gain_m"] or 0

    header_map = {
        "avg_speed_kmh": "Avg Speed (km/h)",
        "distance_km": "Distance (km)",
        "elevation_m": "Elevation (m)",
    }
    lines = [f"{'Week':<12} {header_map[metric]}"]
    for label in _trailing_weeks(weeks):
        b = buckets.get(label)
        if b is None or b["time"] == 0:
            lines.append(f"{label:<12} —")
        elif metric == "avg_speed_kmh":
            val = round(b["dist"] / b["time"] * 3.6, 1)
            lines.append(f"{label:<12} {val}")
        elif metric == "distance_km":
            lines.append(f"{label:<12} {round(b['dist'] / 1000, 1)}")
        else:
            lines.append(f"{label:<12} {round(b['elev'])}")

    return "\n".join(lines)


@mcp.tool()
def get_weekly_volume(
    sport_type: str | None,
    weeks: int = 12,
) -> str:
    """Weekly distance and elevation totals from local DB.

    Args:
        sport_type: Strava sport type or None for all.
        weeks: number of trailing ISO weeks.

    Returns:
        Formatted table with km and elevation per week.
    """
    conn = get_conn()
    init_db(conn)
    rows = get_db_activities(conn, sport_type=sport_type)

    buckets: dict[str, dict] = {}
    for row in rows:
        local_date = date.fromisoformat(row["start_date_local"][:10])
        label = _iso_week_label(local_date)
        if label not in buckets:
            buckets[label] = {"dist": 0.0, "elev": 0.0}
        buckets[label]["dist"] += row["distance_m"] or 0
        buckets[label]["elev"] += row["elevation_gain_m"] or 0

    lines = [f"{'Week':<12} {'km':>8} {'Elevation (m)':>14}"]
    for label in _trailing_weeks(weeks):
        b = buckets.get(label)
        if b is None:
            lines.append(f"{label:<12} {'—':>8} {'—':>14}")
        else:
            lines.append(f"{label:<12} {round(b['dist'] / 1000, 1):>8} {round(b['elev']):>14}")

    return "\n".join(lines)


@mcp.tool()
def get_personal_bests(sport_type: str | None) -> str:
    """Personal bests from all synced activities.

    Args:
        sport_type: Strava sport type or None for all.

    Returns:
        Formatted string with longest distance, duration, fastest speed, most elevation.
    """
    conn = get_conn()
    init_db(conn)
    rows = get_db_activities(conn, sport_type=sport_type)

    if not rows:
        return "No activities found. Run: uv run sync.py"

    best_dist = max(rows, key=lambda r: r["distance_m"] or 0)
    best_dur = max(rows, key=lambda r: r["moving_time_s"] or 0)
    best_speed = max(rows, key=lambda r: r["avg_speed_ms"] or 0)
    best_elev = max(rows, key=lambda r: r["elevation_gain_m"] or 0)

    def fmt(row, extra):
        d = date.fromisoformat(row["start_date_local"][:10])
        return f"{row['name']} ({d}) — {extra}"

    return "\n".join([
        f"longest_distance:  {fmt(best_dist, f'{round(best_dist[\"distance_m\"] / 1000, 1)} km')}",
        f"longest_duration:  {fmt(best_dur, f'{round(best_dur[\"moving_time_s\"] / 60)} min')}",
        f"fastest_avg_speed: {fmt(best_speed, f'{round(best_speed[\"avg_speed_ms\"] * 3.6, 1)} km/h')}",
        f"most_elevation:    {fmt(best_elev, f'{round(best_elev[\"elevation_gain_m\"])} m')}",
    ])
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/user/Dokumente/strava-mcp && uv run pytest tests/test_analytics.py -v
```

Expected: all 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git -C /home/user/Dokumente/strava-mcp add main.py tests/test_analytics.py
git -C /home/user/Dokumente/strava-mcp commit -m "feat: add get_performance_trend, get_weekly_volume, get_personal_bests MCP tools"
```

---

## Task 5: Dashboard (`dashboard.py`)

**Files:**
- Create: `dashboard.py`
- Create: `tests/test_dashboard.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_dashboard.py
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
    # Chart.js must NOT be loaded in empty state
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
    # With only a handful of activities, most weeks should be null
    null_count = sum(1 for v in data["speed"] if v is None)
    assert null_count > 40


def test_api_data_all_sports(db_with_activities):
    with patch("dashboard.get_conn", return_value=db_with_activities):
        from dashboard import app
        client = TestClient(app)
        resp = client.get("/api/data?weeks=4")
    assert resp.status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/user/Dokumente/strava-mcp && uv run pytest tests/test_dashboard.py -v
```

Expected: `ModuleNotFoundError: No module named 'dashboard'`

- [ ] **Step 3: Implement `dashboard.py`**

```python
# dashboard.py
"""Local performance dashboard — FastAPI + Chart.js at localhost:8080."""

import webbrowser
from datetime import date, timedelta
from typing import Optional

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from lib.db import get_conn, init_db, get_activities

app = FastAPI()


def _iso_week_label(d: date) -> str:
    iso = d.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _trailing_weeks(n: int) -> list[str]:
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    return [_iso_week_label(monday - timedelta(weeks=i)) for i in range(n - 1, -1, -1)]


SPORT_LABEL_MAP = {"GravelRide": "GravelRide", "Run": "Run", "": None, None: None}


@app.get("/api/data")
def api_data(sport_type: Optional[str] = None, weeks: int = 12):
    conn = get_conn()
    init_db(conn)
    rows = get_activities(conn, sport_type=sport_type or None)

    buckets: dict[str, dict] = {}
    for row in rows:
        label = _iso_week_label(date.fromisoformat(row["start_date_local"][:10]))
        if label not in buckets:
            buckets[label] = {"dist": 0.0, "time": 0, "elev": 0.0}
        buckets[label]["dist"] += row["distance_m"] or 0
        buckets[label]["time"] += row["moving_time_s"] or 0
        buckets[label]["elev"] += row["elevation_gain_m"] or 0

    labels = _trailing_weeks(weeks)
    speed, volume_km, eas = [], [], []

    for label in labels:
        b = buckets.get(label)
        if b is None or b["time"] == 0:
            speed.append(None)
            volume_km.append(None)
            eas.append(None)
        else:
            avg_spd = b["dist"] / b["time"] * 3.6
            hm_per_km = b["elev"] / (b["dist"] / 1000) if b["dist"] > 0 else 0
            speed.append(round(avg_spd, 2))
            volume_km.append(round(b["dist"] / 1000, 1))
            eas.append(round(avg_spd + hm_per_km * 0.04, 2))

    return {"labels": labels, "speed": speed, "volume_km": volume_km, "eas": eas}


@app.get("/", response_class=HTMLResponse)
def root():
    conn = get_conn()
    init_db(conn)
    count = conn.execute("SELECT COUNT(*) FROM activities").fetchone()[0]

    if count == 0:
        return HTMLResponse("""
<!DOCTYPE html><html><body style="font-family:sans-serif;display:flex;
justify-content:center;align-items:center;height:100vh;margin:0;background:#f5f5f5">
<div style="text-align:center;padding:2rem;background:#fff;border-radius:8px;box-shadow:0 2px 8px rgba(0,0,0,.1)">
  <h2>No data yet</h2>
  <p>Run: <code style="background:#f0f0f0;padding:.2rem .5rem;border-radius:4px">uv run sync.py</code></p>
</div></body></html>""")

    return HTMLResponse("""
<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8">
  <title>Strava Performance Dashboard</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
  <style>
    * { box-sizing: border-box; }
    body { font-family: system-ui, sans-serif; margin: 0; padding: 1.5rem; background: #f5f5f5; }
    h1 { margin: 0 0 1rem; font-size: 1.4rem; }
    .controls { display: flex; gap: 1rem; margin-bottom: 1.5rem; align-items: center; }
    select { padding: .4rem .8rem; border: 1px solid #ccc; border-radius: 6px; font-size: 1rem; }
    .charts { display: grid; gap: 1.5rem; }
    .card { background: #fff; border-radius: 8px; padding: 1.25rem; box-shadow: 0 1px 4px rgba(0,0,0,.08); }
    .card h3 { margin: 0 0 1rem; font-size: 1rem; color: #333; }
    canvas { max-height: 240px; }
  </style>
</head>
<body>
  <h1>🚴 Gravel Performance Dashboard</h1>
  <div class="controls">
    <select id="sport">
      <option value="">Alle</option>
      <option value="GravelRide" selected>Gravel</option>
      <option value="Run">Laufen</option>
    </select>
    <select id="weeks">
      <option value="4">4 Wochen</option>
      <option value="12" selected>12 Wochen</option>
      <option value="26">26 Wochen</option>
      <option value="52">52 Wochen</option>
    </select>
  </div>
  <div class="charts">
    <div class="card"><h3>Ø Tempo pro Woche (km/h)</h3><canvas id="c1"></canvas></div>
    <div class="card"><h3>Distanz pro Woche (km)</h3><canvas id="c2"></canvas></div>
    <div class="card"><h3>Elevation Adjusted Speed (km/h)</h3><canvas id="c3"></canvas></div>
  </div>
  <script>
    const COLORS = { line: '#3b82f6', bar: '#10b981', eas: '#8b5cf6' };
    const cfg = (labels, data, type, color) => ({
      type, data: {
        labels,
        datasets: [{ data, borderColor: color, backgroundColor: type === 'bar' ? color + '99' : color,
          fill: false, tension: 0.3, spanGaps: false, pointRadius: 3 }]
      },
      options: { responsive: true, plugins: { legend: { display: false } },
        scales: { y: { beginAtZero: false } } }
    });

    let charts = [];
    async function load() {
      charts.forEach(c => c.destroy()); charts = [];
      const sport = document.getElementById('sport').value;
      const weeks = document.getElementById('weeks').value;
      const r = await fetch(`/api/data?sport_type=${sport}&weeks=${weeks}`);
      const d = await r.json();
      charts.push(new Chart('c1', cfg(d.labels, d.speed, 'line', COLORS.line)));
      charts.push(new Chart('c2', cfg(d.labels, d.volume_km, 'bar', COLORS.bar)));
      charts.push(new Chart('c3', cfg(d.labels, d.eas, 'line', COLORS.eas)));
    }
    document.getElementById('sport').addEventListener('change', load);
    document.getElementById('weeks').addEventListener('change', load);
    load();
  </script>
</body>
</html>
""")


if __name__ == "__main__":
    webbrowser.open("http://localhost:8080")
    uvicorn.run(app, host="127.0.0.1", port=8080)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/user/Dokumente/strava-mcp && uv run pytest tests/test_dashboard.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 5: Run full test suite**

```bash
cd /home/user/Dokumente/strava-mcp && uv run pytest -v
```

Expected: all tests PASS (no failures).

- [ ] **Step 6: Smoke test — open dashboard in browser**

```bash
cd /home/user/Dokumente/strava-mcp && uv run dashboard.py
```

Expected: browser opens at `localhost:8080`, charts load with Gravel data.

- [ ] **Step 7: Commit**

```bash
git -C /home/user/Dokumente/strava-mcp add dashboard.py tests/test_dashboard.py
git -C /home/user/Dokumente/strava-mcp commit -m "feat: add local performance dashboard (FastAPI + Chart.js)"
```

---

## Done

After Task 5 completes:

```bash
# Sync data (first time: --full)
uv run sync.py --full

# View dashboard
uv run dashboard.py

# Ask Claude analytics questions
# → "Wie hat sich mein Gravel-Tempo in den letzten 3 Monaten entwickelt?"
# → get_performance_trend("GravelRide", "avg_speed_kmh", weeks=12)
```

**Navigation fix:** Install Komoot, import GPX route → audio turn-by-turn while Strava records GPS.
