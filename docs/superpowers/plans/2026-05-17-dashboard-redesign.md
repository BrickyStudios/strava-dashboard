# Dashboard Redesign & AI Coach Comments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the existing Chart.js dashboard with a Stitch-designed dark-theme UI that grades each activity (A+→C) using a composite score and shows a one-sentence Claude Haiku coaching comment per ride.

**Architecture:** New `lib/grade.py` computes composite percentile grades on-the-fly; new `lib/ai_coach.py` calls Claude Haiku once per activity and caches the comment in a new `ai_comment` column. The dashboard `/api/data` endpoint is reshaped to return summary, activities, and trend data in a single JSON object. `dashboard.py`'s HTML response is replaced with a Tailwind + Stitch single-page design.

**Tech Stack:** Python 3.12, FastAPI, SQLite, `anthropic` SDK (Haiku), Tailwind CSS via CDN, vanilla JS fetch + SVG sparklines, pytest + FastAPI TestClient.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `lib/db.py` | Modify | Add `ai_comment` migration in `init_db()` |
| `lib/grade.py` | Create | Composite percentile grading |
| `lib/ai_coach.py` | Create | Claude Haiku comment generation + DB storage |
| `sync.py` | Modify | Call `generate_missing_comments()` after sync |
| `dashboard.py` | Rewrite | New `/api/data` shape + Stitch HTML |
| `pyproject.toml` | Modify | Add `anthropic` dependency |
| `.env.example` | Modify | Document `ANTHROPIC_API_KEY` |
| `tests/test_grade.py` | Create | Grade algorithm tests |
| `tests/test_ai_coach.py` | Create | AI comment generation tests |
| `tests/test_dashboard.py` | Rewrite | Tests for new API shape |
| `tests/conftest.py` | Modify | Extend fixtures with more activities for percentile tests |

---

## Task 1: DB migration — add `ai_comment` column

**Files:**
- Modify: `lib/db.py`
- Modify: `tests/conftest.py` (extend `db_with_activities` fixture)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_db.py`:

```python
def test_init_db_adds_ai_comment_column(db):
    # Column should exist after init_db
    cols = [row[1] for row in db.execute("PRAGMA table_info(activities)").fetchall()]
    assert "ai_comment" in cols


def test_init_db_is_idempotent_with_existing_ai_comment(db):
    # Running init_db again on an existing DB must not raise
    from lib.db import init_db
    init_db(db)  # second call — should not crash


def test_ai_comment_round_trip(db):
    from lib.db import upsert_activity
    upsert_activity(db, {
        "id": 99,
        "name": "Test",
        "sport_type": "GravelRide",
        "start_date": "2026-05-01T10:00:00Z",
        "start_date_local": "2026-05-01T12:00:00",
        "distance": 40000.0,
        "moving_time": 6000,
        "total_elevation_gain": 200.0,
        "average_speed": 6.5,
        "max_speed": 15.0,
        "average_heartrate": None,
        "kilojoules": 700.0,
    })
    db.execute("UPDATE activities SET ai_comment = 'Gute Fahrt!' WHERE id = 99")
    db.commit()
    row = db.execute("SELECT ai_comment FROM activities WHERE id = 99").fetchone()
    assert row[0] == "Gute Fahrt!"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/user/Dokumente/strava-mcp && uv run pytest tests/test_db.py::test_init_db_adds_ai_comment_column tests/test_db.py::test_ai_comment_round_trip -v
```

Expected: FAIL — `ai_comment` column does not exist yet.

- [ ] **Step 3: Add `ai_comment` to `lib/db.py`**

In `init_db()`, add the column to the `CREATE TABLE IF NOT EXISTS` statement and add a safe migration after:

```python
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
    # Safe migration for DBs created before ai_comment column existed
    try:
        conn.execute("ALTER TABLE activities ADD COLUMN ai_comment TEXT")
        conn.commit()
    except Exception:
        pass  # Column already exists
```

Also add `ai_comment` to the `upsert_activity` INSERT statement — add it between `kilojoules` and `raw_json`, defaulting to `NULL` (omit from the VALUES list so it uses the column default):

```python
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
```

Note: `upsert_activity` uses `INSERT OR REPLACE` which would wipe `ai_comment` on re-upsert. Keep `ai_comment` out of the upsert — it is only written by `ai_coach.py` via a direct UPDATE.

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_db.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add lib/db.py tests/test_db.py
git commit -m "feat: add ai_comment column to activities table"
```

---

## Task 2: Grading module (`lib/grade.py`)

**Files:**
- Create: `lib/grade.py`
- Create: `tests/test_grade.py`

### Background

EAS (Elevation Adjusted Speed) normalises speed for climbing:
```
eas_kmh = avg_speed_ms * 3.6 + (elevation_gain_m / (distance_m / 1000)) * 0.04
```

Composite score = 50% distance percentile + 30% EAS percentile + 20% elevation percentile.

Percentile rank of x in values = fraction of values strictly less than x (0.0–1.0). Multiply by 100 for 0–100 score.

Score → Grade:  ≥85 → A+,  70–84 → A,  55–69 → B+,  40–54 → B,  <40 → C.

Cold start (<5 same-sport activities): use absolute distance thresholds (GravelRide ≥60km→A+, ≥40→A, ≥25→B+, ≥10→B, else C; Run ≥15→A+, ≥10→A, ≥7→B+, ≥5→B, else C; other ≥20→A+, ≥12→A, ≥8→B+, ≥4→B, else C).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_grade.py`:

```python
import pytest


RIDE = lambda id, dist, time, elev, speed: {
    "id": id, "sport_type": "GravelRide",
    "distance_m": dist, "moving_time_s": time,
    "elevation_gain_m": elev, "avg_speed_ms": speed,
}


def test_percentile_rank_highest_value():
    from lib.grade import percentile_rank
    assert percentile_rank(100, [10, 20, 50, 100]) == 0.75  # 3 of 4 values < 100


def test_percentile_rank_lowest_value():
    from lib.grade import percentile_rank
    assert percentile_rank(10, [10, 20, 50, 100]) == 0.0


def test_percentile_rank_single_element():
    from lib.grade import percentile_rank
    assert percentile_rank(5, [5]) == 0.0


def test_cold_start_gravel_long_ride():
    from lib.grade import compute_grade
    activity = RIDE(1, 70000, 10000, 400, 7.0)
    others = [RIDE(2, 70000, 10000, 400, 7.0)]  # only 1 other → cold start
    assert compute_grade(activity, others) == "A+"


def test_cold_start_gravel_short_ride():
    from lib.grade import compute_grade
    activity = RIDE(1, 8000, 1200, 30, 6.5)
    others = [RIDE(2, 8000, 1200, 30, 6.5)]
    assert compute_grade(activity, others) == "C"


def test_percentile_grade_best_ride():
    from lib.grade import compute_grade
    # One very strong ride vs 9 weak ones
    strong = RIDE(10, 80000, 11000, 500, 7.5)
    weak = [RIDE(i, 20000, 4000, 100, 5.0) for i in range(9)]
    assert compute_grade(strong, weak) == "A+"


def test_percentile_grade_median_ride():
    from lib.grade import compute_grade
    rides = [RIDE(i, 30000 + i * 5000, 5000, 200, 6.0) for i in range(10)]
    median_ride = rides[4]
    others = rides[:4] + rides[5:]
    grade = compute_grade(median_ride, others)
    assert grade in ("B", "B+")


def test_compute_grade_excludes_self():
    from lib.grade import compute_grade
    # Even if the activity is in the others list, result should be stable
    ride = RIDE(1, 50000, 7000, 300, 6.5)
    others = [RIDE(i, 50000, 7000, 300, 6.5) for i in range(8)]
    grade = compute_grade(ride, others)
    assert grade in ("A+", "A", "B+", "B", "C")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_grade.py -v
```

Expected: FAIL — `lib.grade` does not exist.

- [ ] **Step 3: Implement `lib/grade.py`**

```python
"""Composite activity grading: A+ / A / B+ / B / C."""

COLD_START_MIN = 5

COLD_START_THRESHOLDS = {
    "GravelRide": [60_000, 40_000, 25_000, 10_000],
    "Run":        [15_000, 10_000,  7_000,  5_000],
    "_default":   [20_000, 12_000,  8_000,  4_000],
}
GRADE_LABELS = ["A+", "A", "B+", "B", "C"]


def percentile_rank(value: float, values: list[float]) -> float:
    """Fraction of values strictly less than value (0.0–1.0)."""
    if not values:
        return 0.0
    return sum(1 for v in values if v < value) / len(values)


def _eas(activity: dict) -> float:
    speed_kmh = (activity.get("avg_speed_ms") or 0) * 3.6
    dist_km = (activity.get("distance_m") or 0) / 1000
    elev = activity.get("elevation_gain_m") or 0
    hm_per_km = elev / dist_km if dist_km > 0 else 0
    return speed_kmh + hm_per_km * 0.04


def _cold_start_grade(activity: dict) -> str:
    sport = activity.get("sport_type", "_default")
    thresholds = COLD_START_THRESHOLDS.get(sport, COLD_START_THRESHOLDS["_default"])
    dist = activity.get("distance_m") or 0
    for i, threshold in enumerate(thresholds):
        if dist >= threshold:
            return GRADE_LABELS[i]
    return "C"


def _score_to_grade(score: float) -> str:
    if score >= 85:
        return "A+"
    if score >= 70:
        return "A"
    if score >= 55:
        return "B+"
    if score >= 40:
        return "B"
    return "C"


def compute_grade(activity: dict, all_activities: list[dict]) -> str:
    """
    activity: dict with distance_m, moving_time_s, elevation_gain_m, avg_speed_ms, sport_type
    all_activities: other activities of the same sport_type (does not need to exclude self)
    """
    others = [a for a in all_activities if a.get("id") != activity.get("id")]

    if len(others) < COLD_START_MIN:
        return _cold_start_grade(activity)

    distances = [a.get("distance_m") or 0 for a in others]
    eas_values = [_eas(a) for a in others]
    elevations = [a.get("elevation_gain_m") or 0 for a in others]

    dist_score = percentile_rank(activity.get("distance_m") or 0, distances) * 100
    eas_score  = percentile_rank(_eas(activity), eas_values) * 100
    elev_score = percentile_rank(activity.get("elevation_gain_m") or 0, elevations) * 100

    composite = 0.50 * dist_score + 0.30 * eas_score + 0.20 * elev_score
    return _score_to_grade(composite)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_grade.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add lib/grade.py tests/test_grade.py
git commit -m "feat: composite activity grading module"
```

---

## Task 3: AI coach module (`lib/ai_coach.py`)

**Files:**
- Create: `lib/ai_coach.py`
- Create: `tests/test_ai_coach.py`
- Modify: `pyproject.toml` (add `anthropic`)
- Modify: `.env.example` (document `ANTHROPIC_API_KEY`)

- [ ] **Step 1: Add `anthropic` dependency**

In `pyproject.toml`, add to `dependencies`:
```toml
"anthropic>=0.40.0",
```

Then run:
```bash
cd /home/user/Dokumente/strava-mcp && uv sync
```

- [ ] **Step 2: Document `ANTHROPIC_API_KEY` in `.env.example`**

Add to `.env.example`:
```
ANTHROPIC_API_KEY=your_anthropic_api_key_here
```

- [ ] **Step 3: Write the failing tests**

Create `tests/test_ai_coach.py`:

```python
import pytest
import sqlite3
from unittest.mock import patch, MagicMock
from lib.db import init_db, upsert_activity


ACTIVITY = {
    "id": 1,
    "name": "Seen Runde",
    "sport_type": "GravelRide",
    "start_date": "2026-05-16T07:00:00Z",
    "start_date_local": "2026-05-16T09:00:00",
    "distance": 75300.0,
    "moving_time": 11580,
    "total_elevation_gain": 417.0,
    "average_speed": 6.503,
    "max_speed": 18.0,
    "average_heartrate": None,
    "kilojoules": 1376.0,
}


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    upsert_activity(conn, ACTIVITY)
    yield conn
    conn.close()


def _mock_client(text="Tolle Fahrt! Dein bestes Ergebnis diese Woche."):
    mock = MagicMock()
    mock.messages.create.return_value.content = [MagicMock(text=text)]
    return mock


def test_generate_comment_returns_string():
    from lib.ai_coach import generate_comment
    row = dict(ACTIVITY)
    row["distance_m"] = row.pop("distance")
    row["moving_time_s"] = row.pop("moving_time")
    row["elevation_gain_m"] = row.pop("total_elevation_gain")
    row["avg_speed_ms"] = row.pop("average_speed")
    with patch("lib.ai_coach.anthropic.Anthropic", return_value=_mock_client()):
        result = generate_comment(row, "A+")
    assert isinstance(result, str)
    assert len(result) > 5


def test_generate_comment_returns_none_on_api_error():
    from lib.ai_coach import generate_comment
    row = {"id": 1, "name": "Test", "sport_type": "GravelRide",
           "distance_m": 40000, "moving_time_s": 6000,
           "elevation_gain_m": 200, "avg_speed_ms": 6.5}
    with patch("lib.ai_coach.anthropic.Anthropic") as MockClient:
        MockClient.return_value.messages.create.side_effect = Exception("API error")
        result = generate_comment(row, "B")
    assert result is None


def test_generate_missing_comments_fills_nulls(db):
    from lib.ai_coach import generate_missing_comments
    with patch("lib.ai_coach.generate_comment", return_value="Starke Leistung!"):
        generate_missing_comments(db)
    row = db.execute("SELECT ai_comment FROM activities WHERE id = 1").fetchone()
    assert row[0] == "Starke Leistung!"


def test_generate_missing_comments_skips_existing(db):
    db.execute("UPDATE activities SET ai_comment = 'Already set' WHERE id = 1")
    db.commit()
    from lib.ai_coach import generate_missing_comments
    with patch("lib.ai_coach.generate_comment") as mock_gen:
        generate_missing_comments(db)
    mock_gen.assert_not_called()


def test_generate_missing_comments_skips_on_none_response(db):
    from lib.ai_coach import generate_missing_comments
    with patch("lib.ai_coach.generate_comment", return_value=None):
        generate_missing_comments(db)
    row = db.execute("SELECT ai_comment FROM activities WHERE id = 1").fetchone()
    assert row[0] is None  # still NULL, not written
```

- [ ] **Step 4: Run tests to verify they fail**

```bash
uv run pytest tests/test_ai_coach.py -v
```

Expected: FAIL — `lib.ai_coach` does not exist.

- [ ] **Step 5: Implement `lib/ai_coach.py`**

```python
"""Generate and cache AI coaching comments via Claude Haiku."""

import sqlite3
import anthropic
from pathlib import Path

from dotenv import dotenv_values

from lib.grade import compute_grade
from lib.db import get_activities

ENV_PATH = Path(__file__).parent.parent / ".env"
MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 80


def _get_api_key() -> str:
    return dotenv_values(ENV_PATH).get("ANTHROPIC_API_KEY", "")


def generate_comment(activity: dict, grade: str) -> str | None:
    dist_km = (activity.get("distance_m") or 0) / 1000
    duration_min = (activity.get("moving_time_s") or 0) / 60
    speed_kmh = (activity.get("avg_speed_ms") or 0) * 3.6
    elev = activity.get("elevation_gain_m") or 0

    prompt = (
        f"Du bist ein erfahrener Radtrainer. Gib einen kurzen, motivierenden Kommentar "
        f"(1–2 Sätze, Deutsch, Du-Form) zu dieser Einheit:\n\n"
        f"Sport: {activity.get('sport_type', 'Unbekannt')}\n"
        f"Name: {activity.get('name', '')}\n"
        f"Distanz: {dist_km:.1f} km\n"
        f"Dauer: {duration_min:.0f} min\n"
        f"Durchschnittstempo: {speed_kmh:.1f} km/h\n"
        f"Höhenmeter: {elev:.0f} m\n"
        f"Note: {grade}\n\n"
        f"Nur der Kommentar, keine Einleitung."
    )

    try:
        client = anthropic.Anthropic(api_key=_get_api_key())
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()
    except Exception as e:
        print(f"AI comment generation failed for activity {activity.get('id')}: {e}")
        return None


def generate_missing_comments(conn: sqlite3.Connection) -> None:
    """Generate and store comments for activities that have none. Max 20 per call."""
    rows = conn.execute(
        "SELECT * FROM activities WHERE ai_comment IS NULL "
        "ORDER BY start_date_local DESC LIMIT 20"
    ).fetchall()

    for row in rows:
        activity = dict(row)
        sport = activity.get("sport_type")
        all_same_sport = get_activities(conn, sport_type=sport)
        grade = compute_grade(activity, [dict(a) for a in all_same_sport])
        comment = generate_comment(activity, grade)
        if comment is not None:
            conn.execute(
                "UPDATE activities SET ai_comment = ? WHERE id = ?",
                (comment, activity["id"]),
            )
            conn.commit()
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
uv run pytest tests/test_ai_coach.py -v
```

Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add lib/ai_coach.py tests/test_ai_coach.py pyproject.toml .env.example
git commit -m "feat: AI coach comment generation via Claude Haiku"
```

---

## Task 4: Wire AI comments into sync

**Files:**
- Modify: `sync.py`
- Modify: `tests/test_sync.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_sync.py` (the `from unittest.mock import patch, MagicMock` import is already at the top of that file):

```python
def test_sync_calls_generate_missing_comments(tmp_path):
    """After a successful sync, generate_missing_comments is called."""
    from sync import main
    env_file = tmp_path / ".env"
    env_file.write_text(
        "STRAVA_CLIENT_ID=1\nSTRAVA_CLIENT_SECRET=s\n"
        "STRAVA_ACCESS_TOKEN=t\nSTRAVA_REFRESH_TOKEN=r\n"
    )
    mock_resp = MagicMock()
    mock_resp.json.side_effect = [
        [{"id": 99, "name": "Test", "sport_type": "GravelRide",
          "start_date": "2026-05-01T10:00:00Z", "start_date_local": "2026-05-01T12:00:00",
          "distance": 40000.0, "moving_time": 6000, "total_elevation_gain": 200.0,
          "average_speed": 6.5, "max_speed": 15.0, "kilojoules": 700.0}],
        [],  # second page → empty → stop
    ]
    mock_resp.headers = {"X-RateLimit-Limit": "100,1000", "X-RateLimit-Usage": "1,1"}
    mock_resp.raise_for_status.return_value = None

    mock_token_resp = MagicMock()
    mock_token_resp.json.return_value = {"access_token": "new", "refresh_token": "new_r"}
    mock_token_resp.raise_for_status.return_value = None

    import sys
    with patch("sys.argv", ["sync.py"]), \
         patch("httpx.post", return_value=mock_token_resp), \
         patch("httpx.get", return_value=mock_resp), \
         patch("sync.ENV_PATH", env_file), \
         patch("lib.ai_coach.generate_missing_comments") as mock_gen_comments:
        main()

    mock_gen_comments.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_sync.py::test_sync_calls_generate_missing_comments -v
```

Expected: FAIL — `generate_missing_comments` is not called yet.

- [ ] **Step 3: Add the call to `sync.py`**

In `sync.py`, add the import at the top:
```python
from lib.ai_coach import generate_missing_comments
```

In `main()`, add the call after `set_sync_state`:
```python
    set_sync_state(conn, epoch=int(datetime.now(timezone.utc).timestamp()), count=total_count)
    print(f"Sync complete. {synced} new activities. {total_count} total in DB.")

    print("Generating AI coaching comments for new activities...")
    generate_missing_comments(conn)
    print("Done.")
```

- [ ] **Step 4: Run all sync tests**

```bash
uv run pytest tests/test_sync.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add sync.py tests/test_sync.py
git commit -m "feat: generate AI comments after each sync"
```

---

## Task 5: New `/api/data` endpoint

**Files:**
- Modify: `dashboard.py` (API endpoint only — HTML rewrite is Task 6)
- Rewrite: `tests/test_dashboard.py`
- Modify: `tests/conftest.py` (add `db_with_many_activities` fixture)

**New response shape** (see spec for full example):
```json
{
  "week_label": "2026-W20",
  "summary": { "total_km": …, "avg_speed_kmh": …, "elevation_m": …,
               "km_vs_prev_week_pct": …, "speed_vs_prev_week_pct": …, "elevation_vs_prev_week_pct": …,
               "sparklines": { "km": […8 values…], "speed_eas": […], "elevation": […] } },
  "activities": [ { "id":…, "name":…, "sport_type":…, "date":…, "distance_km":…,
                    "duration_min":…, "avg_speed_kmh":…, "grade":…, "ai_comment":… } ],
  "trends": { "labels": […], "speed_eas": […], "volume_km": […] }
}
```

- [ ] **Step 1: Extend conftest with a richer fixture**

Also update the existing `db` fixture in `tests/conftest.py` to add `check_same_thread=False` (FastAPI's TestClient runs in a separate thread and will raise otherwise):

```python
@pytest.fixture
def db():
    """In-memory SQLite DB, fully initialized."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    yield conn
    conn.close()
```

Then add the new fixture to `tests/conftest.py`:

```python
@pytest.fixture
def db_with_many_activities(db):
    """10 GravelRide activities across multiple weeks for percentile grading tests."""
    import json
    rides = [
        {"id": i, "name": f"Ride {i}", "sport_type": "GravelRide",
         "start_date": f"2026-04-{14+i:02d}T10:00:00Z",
         "start_date_local": f"2026-04-{14+i:02d}T12:00:00",
         "distance": (20000 + i * 6000),
         "moving_time": 3600 + i * 500,
         "total_elevation_gain": 100 + i * 30,
         "average_speed": 5.5 + i * 0.1,
         "max_speed": 15.0, "average_heartrate": None, "kilojoules": 500 + i * 80}
        for i in range(10)
    ]
    for r in rides:
        upsert_activity(db, r)
    return db
```

- [ ] **Step 2: Write the failing tests**

Replace `tests/test_dashboard.py` with:

```python
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient


def test_root_empty_state(db):
    with patch("dashboard.get_conn", return_value=db):
        import importlib, dashboard
        importlib.reload(dashboard)
        client = TestClient(dashboard.app)
        resp = client.get("/")
    assert resp.status_code == 200
    assert "uv run sync.py" in resp.text


def test_root_has_tailwind(db_with_many_activities):
    with patch("dashboard.get_conn", return_value=db_with_many_activities):
        import importlib, dashboard
        importlib.reload(dashboard)
        client = TestClient(dashboard.app)
        resp = client.get("/")
    assert resp.status_code == 200
    assert "tailwindcss" in resp.text.lower()


def test_api_data_shape(db_with_many_activities):
    with patch("dashboard.get_conn", return_value=db_with_many_activities):
        import importlib, dashboard
        importlib.reload(dashboard)
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
        import importlib, dashboard
        importlib.reload(dashboard)
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
        import importlib, dashboard
        importlib.reload(dashboard)
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
        import importlib, dashboard
        importlib.reload(dashboard)
        client = TestClient(dashboard.app)
        resp = client.get("/api/data?weeks=12")
    trends = resp.json()["trends"]
    assert len(trends["labels"]) == 12
    assert len(trends["speed_eas"]) == 12
    assert len(trends["volume_km"]) == 12


def test_api_sport_filter(db_with_many_activities):
    with patch("dashboard.get_conn", return_value=db_with_many_activities):
        import importlib, dashboard
        importlib.reload(dashboard)
        client = TestClient(dashboard.app)
        resp = client.get("/api/data?sport_type=Run&weeks=4")
    # No Run activities in fixture → activities list empty, summary zeros
    data = resp.json()
    assert data["activities"] == []
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
uv run pytest tests/test_dashboard.py -v
```

Expected: most FAIL — API shape doesn't match yet.

- [ ] **Step 4: Rewrite `dashboard.py` API endpoint**

Replace the entire `dashboard.py` with the new implementation. The file has two parts: the API logic (this task) and the HTML (Task 6). Write them together — here is the full file:

```python
"""Local performance dashboard — FastAPI + Tailwind at localhost:8080."""

import webbrowser
from datetime import date, timedelta
from typing import Optional

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from lib.db import get_conn, init_db, get_activities
from lib.grade import compute_grade

app = FastAPI()


def _iso_week_label(d: date) -> str:
    iso = d.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _trailing_weeks(n: int) -> list[str]:
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    return [_iso_week_label(monday - timedelta(weeks=i)) for i in range(n - 1, -1, -1)]


def _eas_kmh(row: dict) -> float:
    speed = (row.get("avg_speed_ms") or 0) * 3.6
    dist_km = (row.get("distance_m") or 0) / 1000
    elev = row.get("elevation_gain_m") or 0
    hm_per_km = elev / dist_km if dist_km > 0 else 0
    return speed + hm_per_km * 0.04


def _week_buckets(rows: list, n_weeks: int) -> tuple[list, dict]:
    labels = _trailing_weeks(n_weeks)
    buckets: dict[str, dict] = {}
    for row in rows:
        row = dict(row)
        label = _iso_week_label(date.fromisoformat(row["start_date_local"][:10]))
        if label not in buckets:
            buckets[label] = {"dist": 0.0, "time": 0, "elev": 0.0}
        buckets[label]["dist"] += row.get("distance_m") or 0
        buckets[label]["time"] += row.get("moving_time_s") or 0
        buckets[label]["elev"] += row.get("elevation_gain_m") or 0
    return labels, buckets


def _pct_change(current, previous) -> Optional[int]:
    if previous and previous != 0:
        return round((current - previous) / previous * 100)
    return None


@app.get("/api/data")
def api_data(sport_type: Optional[str] = None, weeks: int = 12):
    conn = get_conn()
    init_db(conn)
    rows = get_activities(conn, sport_type=sport_type or None)
    row_dicts = [dict(r) for r in rows]

    # --- Week buckets for trends and summary ---
    labels, buckets = _week_buckets(rows, weeks)

    speed_eas_series, volume_km_series = [], []
    for label in labels:
        b = buckets.get(label)
        if b is None or b["time"] == 0:
            speed_eas_series.append(None)
            volume_km_series.append(None)
        else:
            avg_spd = b["dist"] / b["time"] * 3.6
            hm_per_km = b["elev"] / (b["dist"] / 1000) if b["dist"] > 0 else 0
            speed_eas_series.append(round(avg_spd + hm_per_km * 0.04, 2))
            volume_km_series.append(round(b["dist"] / 1000, 1))

    # --- Current and previous ISO week for summary ---
    today = date.today()
    cur_week = _iso_week_label(today)
    prev_monday = today - timedelta(days=today.weekday() + 7)
    prev_week = _iso_week_label(prev_monday)

    cur = buckets.get(cur_week, {"dist": 0, "time": 0, "elev": 0})
    prev = buckets.get(prev_week, {"dist": 0, "time": 0, "elev": 0})

    cur_km = cur["dist"] / 1000
    cur_speed = (cur["dist"] / cur["time"] * 3.6) if cur["time"] > 0 else 0
    cur_hm_per_km = cur["elev"] / (cur["dist"] / 1000) if cur["dist"] > 0 else 0
    cur_eas = cur_speed + cur_hm_per_km * 0.04
    cur_elev = cur["elev"]

    prev_km = prev["dist"] / 1000
    prev_speed = (prev["dist"] / prev["time"] * 3.6) if prev["time"] > 0 else 0
    prev_hm_per_km = prev["elev"] / (prev["dist"] / 1000) if prev["dist"] > 0 else 0
    prev_eas = prev_speed + prev_hm_per_km * 0.04
    prev_elev = prev["elev"]

    # --- Sparklines: last 8 weeks ---
    spark_labels = _trailing_weeks(8)
    _, spark_buckets = _week_buckets(rows, 8)
    spark_km, spark_eas, spark_elev = [], [], []
    for lbl in spark_labels:
        b = spark_buckets.get(lbl)
        if b and b["time"] > 0:
            spd = b["dist"] / b["time"] * 3.6
            hm_pk = b["elev"] / (b["dist"] / 1000) if b["dist"] > 0 else 0
            spark_km.append(round(b["dist"] / 1000, 1))
            spark_eas.append(round(spd + hm_pk * 0.04, 2))
            spark_elev.append(round(b["elev"], 0))
        else:
            spark_km.append(None)
            spark_eas.append(None)
            spark_elev.append(None)

    # --- Activities: 10 most recent ---
    recent = sorted(row_dicts, key=lambda r: r.get("start_date_local") or "", reverse=True)[:10]
    activity_list = []
    for r in recent:
        grade = compute_grade(r, row_dicts)
        start = (r.get("start_date_local") or "")[:10]
        try:
            d = date.fromisoformat(start)
            date_str = f"{d.day:02d}.{d.month:02d}."
        except Exception:
            date_str = start
        activity_list.append({
            "id": r.get("id"),
            "name": r.get("name") or "",
            "sport_type": r.get("sport_type") or "",
            "date": date_str,
            "distance_km": round((r.get("distance_m") or 0) / 1000, 1),
            "duration_min": round((r.get("moving_time_s") or 0) / 60),
            "avg_speed_kmh": round(_eas_kmh(r), 1),
            "grade": grade,
            "ai_comment": r.get("ai_comment"),
        })

    return {
        "week_label": cur_week,
        "summary": {
            "total_km": round(cur_km, 1),
            "avg_speed_kmh": round(cur_eas, 1),
            "elevation_m": round(cur_elev),
            "km_vs_prev_week_pct": _pct_change(cur_km, prev_km),
            "speed_vs_prev_week_pct": _pct_change(cur_eas, prev_eas),
            "elevation_vs_prev_week_pct": _pct_change(cur_elev, prev_elev),
            "sparklines": {
                "km": spark_km,
                "speed_eas": spark_eas,
                "elevation": spark_elev,
            },
        },
        "activities": activity_list,
        "trends": {
            "labels": labels,
            "speed_eas": speed_eas_series,
            "volume_km": volume_km_series,
        },
    }


@app.get("/", response_class=HTMLResponse)
def root():
    conn = get_conn()
    init_db(conn)
    count = conn.execute("SELECT COUNT(*) FROM activities").fetchone()[0]
    if count == 0:
        return HTMLResponse(_empty_html())
    return HTMLResponse(_dashboard_html())


def _empty_html() -> str:
    return """<!DOCTYPE html>
<html lang="de">
<head><meta charset="UTF-8"><title>Strava Dashboard</title>
<script src="https://cdn.tailwindcss.com"></script></head>
<body class="bg-[#131316] text-[#e4e1e6] flex items-center justify-center min-h-screen">
  <div class="text-center">
    <h2 class="text-2xl font-semibold mb-4">Keine Daten</h2>
    <p class="text-[#c4c9ac] mb-2">Sync starten:</p>
    <code class="bg-[#1f1f22] px-3 py-1 rounded text-[#abd600]">uv run sync.py</code>
  </div>
</body></html>"""


def _dashboard_html() -> str:
    # Implemented in Task 6
    return _empty_html()


if __name__ == "__main__":
    webbrowser.open("http://localhost:8080")
    uvicorn.run(app, host="127.0.0.1", port=8080)
```

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/test_dashboard.py -v
```

Expected: all PASS (HTML tests pass because `_dashboard_html` still calls `_empty_html` — that gets fixed in Task 6).

- [ ] **Step 6: Run full suite**

```bash
uv run pytest -v
```

Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add dashboard.py tests/test_dashboard.py tests/conftest.py
git commit -m "feat: new /api/data endpoint with grades, comments, sparklines"
```

---

## Task 6: Dashboard HTML redesign (Stitch design system)

**Files:**
- Modify: `dashboard.py` — replace `_dashboard_html()` stub

No new tests — the `test_root_has_tailwind` test from Task 5 already covers the HTML presence check. Open the browser to verify visually.

- [ ] **Step 1: Replace `_dashboard_html()` in `dashboard.py`**

Replace the stub function with the full implementation:

```python
def _dashboard_html() -> str:
    return """<!DOCTYPE html>
<html lang="de" class="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Gravel Dashboard</title>
  <script src="https://cdn.tailwindcss.com?plugins=forms"></script>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap" rel="stylesheet">
  <link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:wght,FILL@400,0..1&display=swap" rel="stylesheet">
  <script>
    tailwind.config = {
      darkMode: 'class',
      theme: {
        extend: {
          colors: {
            'bg':       '#131316',
            'surface':  '#1f1f22',
            'surface-low': '#1b1b1e',
            'surface-high': '#2a2a2d',
            'border':   '#353438',
            'on-surface': '#e4e1e6',
            'muted':    '#c4c9ac',
            'lime':     '#abd600',
            'lime-bright': '#c3f400',
            'orange':   '#f18400',
          },
          fontFamily: { sans: ['Inter', 'system-ui', 'sans-serif'] },
        }
      }
    }
  </script>
  <style>
    body { font-family: 'Inter', system-ui, sans-serif; }
    .grade-ap { background:#c3f400; color:#161e00; }
    .grade-a  { background:#abd60099; color:#161e00; }
    .grade-bp { background:#f18400; color:#2f1500; }
    .grade-b  { background:#2a2a2d; color:#e4e1e6; border:1px solid #353438; }
    .grade-c  { background:#1b1b1e; color:#8e9379; border:1px solid #353438; }
  </style>
</head>
<body class="bg-bg text-on-surface min-h-screen">

  <!-- Header -->
  <header class="sticky top-0 z-10 bg-bg/80 backdrop-blur border-b border-border px-6 py-3 flex items-center justify-between">
    <h1 class="text-lg font-bold tracking-tight text-lime">GRAVEL DASHBOARD</h1>
    <div class="flex gap-3 items-center">
      <select id="sport" class="bg-surface border border-border text-on-surface text-sm rounded px-2 py-1 focus:outline-none focus:border-lime">
        <option value="">Alle</option>
        <option value="GravelRide" selected>Gravel</option>
        <option value="Run">Laufen</option>
      </select>
      <select id="weeks" class="bg-surface border border-border text-on-surface text-sm rounded px-2 py-1 focus:outline-none focus:border-lime">
        <option value="4">4 Wochen</option>
        <option value="12" selected>12 Wochen</option>
        <option value="26">26 Wochen</option>
        <option value="52">52 Wochen</option>
      </select>
    </div>
  </header>

  <main class="max-w-3xl mx-auto px-4 py-6 space-y-8">

    <!-- Weekly Summary -->
    <section>
      <div class="flex justify-between items-baseline mb-3">
        <h2 class="text-base font-semibold text-on-surface">Wochenzusammenfassung</h2>
        <span id="week-label" class="text-xs text-muted"></span>
      </div>
      <div class="grid grid-cols-3 gap-3">
        <div class="bg-surface-low border border-border rounded-lg p-4 flex flex-col">
          <span class="text-xs font-bold uppercase tracking-wider text-muted mb-3">Distanz</span>
          <div class="flex items-baseline gap-1 mb-3">
            <span id="s-km" class="text-3xl font-bold text-on-surface">—</span>
            <span class="text-xs text-muted">km</span>
          </div>
          <div id="badge-km" class="text-xs mb-2"></div>
          <svg id="spark-km" class="mt-auto w-full h-8" viewBox="0 0 80 24" preserveAspectRatio="none"></svg>
        </div>
        <div class="bg-surface-low border border-border rounded-lg p-4 flex flex-col">
          <span class="text-xs font-bold uppercase tracking-wider text-muted mb-3">Tempo EAS</span>
          <div class="flex items-baseline gap-1 mb-3">
            <span id="s-speed" class="text-3xl font-bold text-on-surface">—</span>
            <span class="text-xs text-muted">km/h</span>
          </div>
          <div id="badge-speed" class="text-xs mb-2"></div>
          <svg id="spark-speed" class="mt-auto w-full h-8" viewBox="0 0 80 24" preserveAspectRatio="none"></svg>
        </div>
        <div class="bg-surface-low border border-border rounded-lg p-4 flex flex-col">
          <span class="text-xs font-bold uppercase tracking-wider text-muted mb-3">Höhenmeter</span>
          <div class="flex items-baseline gap-1 mb-3">
            <span id="s-elev" class="text-3xl font-bold text-on-surface">—</span>
            <span class="text-xs text-muted">m</span>
          </div>
          <div id="badge-elev" class="text-xs mb-2"></div>
          <svg id="spark-elev" class="mt-auto w-full h-8" viewBox="0 0 80 24" preserveAspectRatio="none"></svg>
        </div>
      </div>
    </section>

    <!-- Activity Feed -->
    <section>
      <h2 class="text-base font-semibold text-on-surface mb-3">Letzte Einheiten</h2>
      <div id="activity-feed" class="space-y-2"></div>
    </section>

    <!-- Trends -->
    <section>
      <h2 class="text-base font-semibold text-on-surface mb-3">Trends</h2>
      <div class="grid grid-cols-2 gap-3">
        <div class="bg-surface-low border border-border rounded-lg p-4">
          <div class="flex justify-between items-center mb-2">
            <span class="text-xs font-bold uppercase tracking-wider text-muted">Tempo EAS</span>
            <span id="trend-speed-badge" class="text-xs font-semibold text-lime"></span>
          </div>
          <svg id="trend-speed" class="w-full h-16" viewBox="0 0 200 48" preserveAspectRatio="none"></svg>
        </div>
        <div class="bg-surface-low border border-border rounded-lg p-4">
          <div class="flex justify-between items-center mb-2">
            <span class="text-xs font-bold uppercase tracking-wider text-muted">km / Woche</span>
            <span id="trend-km-badge" class="text-xs font-semibold text-orange"></span>
          </div>
          <svg id="trend-km" class="w-full h-16" viewBox="0 0 200 48" preserveAspectRatio="none"></svg>
        </div>
      </div>
    </section>

  </main>

  <script>
    const GRADE_CLASS = {'A+':'grade-ap','A':'grade-a','B+':'grade-bp','B':'grade-b','C':'grade-c'};
    const SPORT_ICON = {'GravelRide':'directions_bike','Run':'directions_run'};

    function sparklinePath(values, w, h, pad=2) {
      const valid = values.filter(v => v !== null);
      if (!valid.length) return '';
      const mn = Math.min(...valid), mx = Math.max(...valid);
      const range = mx - mn || 1;
      const step = w / (values.length - 1 || 1);
      const pts = values.map((v, i) => {
        if (v === null) return null;
        return [i * step, h - pad - ((v - mn) / range) * (h - pad * 2)];
      });
      let d = '', prev = null;
      for (const pt of pts) {
        if (!pt) { prev = null; continue; }
        d += prev ? `L${pt[0].toFixed(1)},${pt[1].toFixed(1)}` : `M${pt[0].toFixed(1)},${pt[1].toFixed(1)}`;
        prev = pt;
      }
      return d;
    }

    function renderSparkline(svgId, values, color) {
      const svg = document.getElementById(svgId);
      if (!svg) return;
      const vb = svg.getAttribute('viewBox').split(' ');
      const w = +vb[2], h = +vb[3];
      const d = sparklinePath(values, w, h);
      svg.innerHTML = d ? `<path d="${d}" fill="none" stroke="${color}" stroke-width="2" stroke-linecap="round"/>` : '';
    }

    function pctBadge(pct, elId) {
      const el = document.getElementById(elId);
      if (!el || pct === null || pct === undefined) { if(el) el.textContent=''; return; }
      const arrow = pct >= 0 ? '↑' : '↓';
      const cls = pct >= 0 ? 'text-lime' : 'text-orange';
      el.innerHTML = `<span class="${cls}">${arrow} ${Math.abs(pct)}% vs. VW</span>`;
    }

    function gradeEl(grade) {
      const cls = GRADE_CLASS[grade] || 'grade-c';
      return `<div class="w-9 h-9 rounded-lg flex items-center justify-center text-xs font-bold shrink-0 ${cls}">${grade}</div>`;
    }

    function activityCard(act) {
      const icon = SPORT_ICON[act.sport_type] || 'fitness_center';
      const comment = act.ai_comment
        ? `<p class="text-xs text-muted italic mt-1">${act.ai_comment}</p>` : '';
      return `
        <div class="bg-surface-low border border-border rounded-lg p-3 flex items-start gap-3 hover:border-lime/30 transition-colors">
          <div class="w-10 h-10 rounded-full bg-surface-high flex items-center justify-center shrink-0">
            <span class="material-symbols-outlined text-muted text-lg">${icon}</span>
          </div>
          <div class="flex-1 min-w-0">
            <p class="text-sm font-semibold text-on-surface truncate">${act.name}</p>
            <p class="text-xs text-muted">${act.date} · ${act.distance_km} km · ${act.duration_min} min · ${act.avg_speed_kmh} km/h EAS</p>
            ${comment}
          </div>
          ${gradeEl(act.grade)}
        </div>`;
    }

    async function load() {
      const sport = document.getElementById('sport').value;
      const weeks = document.getElementById('weeks').value;
      const r = await fetch(`/api/data?sport_type=${sport}&weeks=${weeks}`);
      const d = await r.json();

      // Summary
      document.getElementById('week-label').textContent = d.week_label;
      document.getElementById('s-km').textContent = d.summary.total_km ?? '—';
      document.getElementById('s-speed').textContent = d.summary.avg_speed_kmh ?? '—';
      document.getElementById('s-elev').textContent = d.summary.elevation_m ?? '—';
      pctBadge(d.summary.km_vs_prev_week_pct, 'badge-km');
      pctBadge(d.summary.speed_vs_prev_week_pct, 'badge-speed');
      pctBadge(d.summary.elevation_vs_prev_week_pct, 'badge-elev');
      renderSparkline('spark-km', d.summary.sparklines.km, '#abd600');
      renderSparkline('spark-speed', d.summary.sparklines.speed_eas, '#abd600');
      renderSparkline('spark-elev', d.summary.sparklines.elevation, '#abd600');

      // Activities
      const feed = document.getElementById('activity-feed');
      feed.innerHTML = d.activities.length
        ? d.activities.map(activityCard).join('')
        : '<p class="text-muted text-sm">Keine Aktivitäten gefunden.</p>';

      // Trends
      renderSparkline('trend-speed', d.trends.speed_eas, '#abd600');
      renderSparkline('trend-km', d.trends.volume_km, '#f18400');

      // Trend badges: compare last vs first non-null
      function trendBadge(series, elId, color) {
        const vals = series.filter(v => v !== null);
        if (vals.length < 2) { document.getElementById(elId).textContent=''; return; }
        const pct = Math.round((vals[vals.length-1] - vals[0]) / vals[0] * 100);
        const el = document.getElementById(elId);
        const arrow = pct >= 0 ? '↑' : '↓';
        el.textContent = `${arrow} ${Math.abs(pct)}%`;
        el.className = `text-xs font-semibold ${color}`;
      }
      trendBadge(d.trends.speed_eas, 'trend-speed-badge', 'text-lime');
      trendBadge(d.trends.volume_km, 'trend-km-badge', 'text-orange');
    }

    document.getElementById('sport').addEventListener('change', load);
    document.getElementById('weeks').addEventListener('change', load);
    load();
  </script>
</body>
</html>"""
```

- [ ] **Step 2: Run all tests**

```bash
uv run pytest -v
```

Expected: all PASS.

- [ ] **Step 3: Start the dashboard and verify visually**

```bash
cd /home/user/Dokumente/strava-mcp && uv run dashboard.py
```

Open http://localhost:8080 and verify:
- Dark background, Electric Lime accents
- 3 summary cards with values and sparklines
- Activity feed with grade badges (A+→C) and AI comments (or blank if NULL)
- Two trend sparklines at the bottom
- Sport / weeks dropdowns work

- [ ] **Step 4: Commit**

```bash
git add dashboard.py
git commit -m "feat: Stitch dashboard redesign with grading and AI coach comments"
```

---

## Final check

```bash
uv run pytest -v
```

All tests green. Dashboard live at http://localhost:8080.
