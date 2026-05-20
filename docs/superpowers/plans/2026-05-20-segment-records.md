# Segment Records & Opportunities — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Segmente" tab to the Strava dashboard showing segments where the user holds KOMs and near-miss record opportunities.

**Architecture:** (1) `lib/db.py` gets a `segment_efforts` table + CRUD functions. (2) `sync.py` fetches per-activity detail data from Strava and stores parsed segment efforts. (3) `dashboard.py` gets a `/api/segments` endpoint and a new "Segmente" tab in the HTML.

**Tech Stack:** Python 3.11+, FastAPI, SQLite, Tailwind CSS (CDN), vanilla JS, pytest, httpx

---

## File Map

| File | Change |
|------|--------|
| `lib/db.py` | Add `segment_efforts` table in `init_db()`, add `upsert_segment_efforts()`, `get_koms()`, `get_all_ranked_efforts()` |
| `sync.py` | Add `parse_segment_effort()`, `sync_segment_efforts()`, call from `main()` |
| `dashboard.py` | Add `/api/segments` endpoint; update `_dashboard_html()` with tab bar + segments tab + JS |
| `tests/test_db.py` | Add tests for new table and DB functions |
| `tests/test_sync.py` | Add tests for `parse_segment_effort()` |
| `tests/test_dashboard.py` | Add tests for `/api/segments` endpoint |

---

## Task 1: DB Layer — segment_efforts table + CRUD

**Files:**
- Modify: `lib/db.py`
- Test: `tests/test_db.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_db.py`:

```python
def test_init_db_creates_segment_efforts_table(db):
    cursor = db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row["name"] for row in cursor}
    assert "segment_efforts" in tables


def test_upsert_segment_efforts_inserts(db):
    from lib.db import upsert_segment_efforts
    upsert_activity(db, {
        "id": 1, "name": "Ride", "sport_type": "GravelRide",
        "start_date": "2026-05-01T10:00:00Z",
        "start_date_local": "2026-05-01T12:00:00",
        "distance": 40000.0, "moving_time": 6000,
        "total_elevation_gain": 200.0, "average_speed": 6.5,
        "max_speed": 15.0, "average_heartrate": None, "kilojoules": 700.0,
    })
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
    upsert_activity(db, {
        "id": 2, "name": "Trainer", "sport_type": "VirtualRide",
        "start_date": "2026-05-02T10:00:00Z",
        "start_date_local": "2026-05-02T12:00:00",
        "distance": 30000.0, "moving_time": 4000,
        "total_elevation_gain": 0.0, "average_speed": 7.5,
        "max_speed": 12.0, "average_heartrate": None, "kilojoules": 500.0,
    })
    upsert_segment_efforts(db, activity_id=2, efforts=[])
    row = db.execute(
        "SELECT * FROM segment_efforts WHERE activity_id=2 AND segment_id=0"
    ).fetchone()
    assert row is not None  # sentinel exists so activity is not re-fetched


def test_get_koms_returns_rank1_segments(db):
    from lib.db import upsert_segment_efforts, get_koms
    upsert_activity(db, {
        "id": 1, "name": "Ride", "sport_type": "GravelRide",
        "start_date": "2026-05-01T10:00:00Z",
        "start_date_local": "2026-05-01T12:00:00",
        "distance": 40000.0, "moving_time": 6000,
        "total_elevation_gain": 200.0, "average_speed": 6.5,
        "max_speed": 15.0, "average_heartrate": None, "kilojoules": 700.0,
    })
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
    upsert_activity(db, {
        "id": 2, "name": "Trainer", "sport_type": "VirtualRide",
        "start_date": "2026-05-02T10:00:00Z",
        "start_date_local": "2026-05-02T12:00:00",
        "distance": 30000.0, "moving_time": 4000,
        "total_elevation_gain": 0.0, "average_speed": 7.5,
        "max_speed": 12.0, "average_heartrate": None, "kilojoules": 500.0,
    })
    upsert_segment_efforts(db, activity_id=2, efforts=[])
    efforts = get_all_ranked_efforts(db)
    assert all(e["segment_id"] != 0 for e in efforts)
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd /home/user/Dokumente/strava-mcp
uv run pytest tests/test_db.py::test_init_db_creates_segment_efforts_table tests/test_db.py::test_upsert_segment_efforts_inserts -v
```
Expected: `FAILED` — `upsert_segment_efforts` not found, table doesn't exist

- [ ] **Step 3: Implement DB changes in `lib/db.py`**

In `init_db()`, add this block after the existing `executescript` + `commit`:
```python
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
```

Add these three functions after `get_activities()`:
```python
def upsert_segment_efforts(conn, activity_id: int, efforts: list[dict]) -> None:
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


def get_koms(conn) -> list:
    return conn.execute("""
        SELECT segment_id, segment_name, segment_distance_m,
               MIN(elapsed_time_s) AS elapsed_time_s,
               MAX(start_date_local) AS activity_date
        FROM segment_efforts
        WHERE overall_rank = 1 AND segment_id != 0
        GROUP BY segment_id
        ORDER BY segment_distance_m DESC
    """).fetchall()


def get_all_ranked_efforts(conn) -> list:
    return conn.execute("""
        SELECT segment_id, segment_name, segment_distance_m,
               elapsed_time_s, overall_rank, start_date_local
        FROM segment_efforts
        WHERE segment_id != 0
        ORDER BY segment_id, start_date_local ASC
    """).fetchall()
```

- [ ] **Step 4: Run all DB tests to verify they pass**

```
uv run pytest tests/test_db.py -v
```
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add lib/db.py tests/test_db.py
git commit -m "feat: add segment_efforts table and CRUD functions to db layer"
```

---

## Task 2: Sync Layer — parse and store segment efforts

**Files:**
- Modify: `sync.py`
- Test: `tests/test_sync.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_sync.py`:
```python
def test_parse_segment_effort_with_kom():
    from sync import parse_segment_effort
    raw = {
        "elapsed_time": 54,
        "start_date_local": "2026-05-19T10:30:00Z",
        "pr_rank": 1,
        "segment": {"id": 999, "name": "Bergkuppe Sprint", "distance": 420.5},
        "achievements": [{"type": "overall", "type_id": 2, "rank": 1}],
    }
    result = parse_segment_effort(raw)
    assert result["segment_id"] == 999
    assert result["segment_name"] == "Bergkuppe Sprint"
    assert result["segment_distance_m"] == 420.5
    assert result["elapsed_time_s"] == 54
    assert result["overall_rank"] == 1
    assert result["pr_rank"] == 1


def test_parse_segment_effort_no_achievement():
    from sync import parse_segment_effort
    raw = {
        "elapsed_time": 120,
        "start_date_local": "2026-05-01T09:00:00Z",
        "pr_rank": None,
        "segment": {"id": 777, "name": "Flat Road", "distance": 1000.0},
        "achievements": [],
    }
    result = parse_segment_effort(raw)
    assert result["segment_id"] == 777
    assert result["overall_rank"] is None


def test_parse_segment_effort_rank2_skips_non_overall():
    from sync import parse_segment_effort
    raw = {
        "elapsed_time": 198,
        "start_date_local": "2026-05-10T08:00:00Z",
        "pr_rank": 1,
        "segment": {"id": 888, "name": "Waldweg Abfahrt", "distance": 1200.0},
        "achievements": [
            {"type": "year_overall", "type_id": 3, "rank": 1},
            {"type": "overall", "type_id": 2, "rank": 2},
        ],
    }
    result = parse_segment_effort(raw)
    assert result["overall_rank"] == 2
```

- [ ] **Step 2: Run tests to verify they fail**

```
uv run pytest tests/test_sync.py::test_parse_segment_effort_with_kom -v
```
Expected: `FAILED` — `parse_segment_effort` not found

- [ ] **Step 3: Implement in `sync.py`**

Add import at the top of `sync.py` (with existing db imports):
```python
from lib.db import get_conn, init_db, upsert_activity, get_sync_state, set_sync_state, upsert_segment_efforts
```

Add these two functions before `main()`:
```python
def parse_segment_effort(effort: dict) -> dict:
    seg = effort.get("segment") or {}
    overall_rank = None
    for a in effort.get("achievements") or []:
        if a.get("type") == "overall":
            overall_rank = a.get("rank")
            break
    return {
        "segment_id": seg.get("id"),
        "segment_name": seg.get("name"),
        "segment_distance_m": seg.get("distance"),
        "elapsed_time_s": effort.get("elapsed_time"),
        "start_date_local": effort.get("start_date_local"),
        "pr_rank": effort.get("pr_rank"),
        "overall_rank": overall_rank,
    }


def sync_segment_efforts(conn: sqlite3.Connection, access_token: str) -> None:
    rows = conn.execute("""
        SELECT id FROM activities
        WHERE id NOT IN (SELECT DISTINCT activity_id FROM segment_efforts)
        ORDER BY start_date_local DESC
    """).fetchall()

    if not rows:
        print("All activities already have segment data.")
        return

    print(f"Fetching segment efforts for {len(rows)} activities...")
    for row in rows:
        activity_id = row[0]
        resp = httpx.get(
            f"https://www.strava.com/api/v3/activities/{activity_id}?include_all_efforts=true",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        check_rate_limit(resp.headers)
        detail = resp.json()
        raw_efforts = detail.get("segment_efforts") or []
        parsed = [parse_segment_effort(e) for e in raw_efforts]
        upsert_segment_efforts(conn, activity_id, parsed)
        print(f"  Activity {activity_id}: {len(parsed)} segment efforts stored")
```

In `main()`, add after the `generate_missing_comments` call:
```python
print("Syncing segment efforts...")
sync_segment_efforts(conn, access_token)
```

- [ ] **Step 4: Run all sync tests to verify they pass**

```
uv run pytest tests/test_sync.py -v
```
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add sync.py tests/test_sync.py
git commit -m "feat: parse and sync segment efforts from Strava detail API"
```

---

## Task 3: API Layer — /api/segments endpoint

**Files:**
- Modify: `dashboard.py`
- Test: `tests/test_dashboard.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_dashboard.py`:
```python
def test_api_segments_shape(db):
    from lib.db import upsert_segment_efforts
    upsert_activity(db, {
        "id": 1, "name": "Ride", "sport_type": "GravelRide",
        "start_date": "2026-05-01T10:00:00Z",
        "start_date_local": "2026-05-01T12:00:00",
        "distance": 40000.0, "moving_time": 6000,
        "total_elevation_gain": 200.0, "average_speed": 6.5,
        "max_speed": 15.0, "average_heartrate": None, "kilojoules": 700.0,
    })
    upsert_segment_efforts(db, activity_id=1, efforts=[
        {"segment_id": 100, "segment_name": "KOM Seg", "segment_distance_m": 500.0,
         "elapsed_time_s": 60, "start_date_local": "2026-05-01T12:30:00",
         "pr_rank": 1, "overall_rank": 1},
        {"segment_id": 200, "segment_name": "2nd Seg", "segment_distance_m": 300.0,
         "elapsed_time_s": 45, "start_date_local": "2026-05-01T12:35:00",
         "pr_rank": 1, "overall_rank": 2},
    ])
    with patch("dashboard.get_conn", return_value=db):
        client = TestClient(dashboard.app)
        resp = client.get("/api/segments")
    assert resp.status_code == 200
    data = resp.json()
    assert "koms" in data
    assert "opportunities" in data
    assert len(data["koms"]) == 1
    assert data["koms"][0]["segment_id"] == 100
    assert len(data["opportunities"]) == 1
    assert data["opportunities"][0]["segment_id"] == 200


def test_api_segments_empty(db):
    with patch("dashboard.get_conn", return_value=db):
        client = TestClient(dashboard.app)
        resp = client.get("/api/segments")
    assert resp.status_code == 200
    data = resp.json()
    assert data == {"koms": [], "opportunities": []}


def test_api_segments_trending_included(db):
    from lib.db import upsert_segment_efforts
    for act_id, date_str in [(1, "2026-05-01"), (2, "2026-05-08")]:
        upsert_activity(db, {
            "id": act_id, "name": f"Ride {act_id}", "sport_type": "GravelRide",
            "start_date": f"{date_str}T10:00:00Z",
            "start_date_local": f"{date_str}T12:00:00",
            "distance": 40000.0, "moving_time": 6000,
            "total_elevation_gain": 200.0, "average_speed": 6.5,
            "max_speed": 15.0, "average_heartrate": None, "kilojoules": 700.0,
        })
    # Segment 300: improving trend (200s → 180s = -10%), no overall rank
    upsert_segment_efforts(db, activity_id=1, efforts=[
        {"segment_id": 300, "segment_name": "Trend Seg", "segment_distance_m": 800.0,
         "elapsed_time_s": 200, "start_date_local": "2026-05-01T12:30:00",
         "pr_rank": 2, "overall_rank": None},
    ])
    upsert_segment_efforts(db, activity_id=2, efforts=[
        {"segment_id": 300, "segment_name": "Trend Seg", "segment_distance_m": 800.0,
         "elapsed_time_s": 180, "start_date_local": "2026-05-08T12:30:00",
         "pr_rank": 1, "overall_rank": None},
    ])
    with patch("dashboard.get_conn", return_value=db):
        client = TestClient(dashboard.app)
        resp = client.get("/api/segments")
    opps = resp.json()["opportunities"]
    assert any(o["segment_id"] == 300 and o["is_trending"] for o in opps)


def test_api_segments_kom_not_in_opportunities(db):
    from lib.db import upsert_segment_efforts
    upsert_activity(db, {
        "id": 1, "name": "Ride", "sport_type": "GravelRide",
        "start_date": "2026-05-01T10:00:00Z",
        "start_date_local": "2026-05-01T12:00:00",
        "distance": 40000.0, "moving_time": 6000,
        "total_elevation_gain": 200.0, "average_speed": 6.5,
        "max_speed": 15.0, "average_heartrate": None, "kilojoules": 700.0,
    })
    upsert_segment_efforts(db, activity_id=1, efforts=[
        {"segment_id": 100, "segment_name": "KOM", "segment_distance_m": 500.0,
         "elapsed_time_s": 60, "start_date_local": "2026-05-01T12:30:00",
         "pr_rank": 1, "overall_rank": 1},
    ])
    with patch("dashboard.get_conn", return_value=db):
        client = TestClient(dashboard.app)
        resp = client.get("/api/segments")
    data = resp.json()
    opp_ids = [o["segment_id"] for o in data["opportunities"]]
    assert 100 not in opp_ids
```

- [ ] **Step 2: Run tests to verify they fail**

```
uv run pytest tests/test_dashboard.py::test_api_segments_shape tests/test_dashboard.py::test_api_segments_empty -v
```
Expected: `FAILED` — endpoint not found

- [ ] **Step 3: Add import and endpoint to `dashboard.py`**

Update the import line (top of file) to include new DB functions:
```python
from lib.db import get_conn, init_db, get_activities, get_koms, get_all_ranked_efforts
```

Add this endpoint before the `root()` function (before `@app.get("/", response_class=HTMLResponse)`):
```python
@app.get("/api/segments")
def api_segments():
    conn = get_conn()
    init_db(conn)

    koms_raw = get_koms(conn)
    kom_ids = {r["segment_id"] for r in koms_raw}
    all_efforts = get_all_ranked_efforts(conn)

    by_seg: dict[int, dict] = {}
    for e in all_efforts:
        sid = e["segment_id"]
        if sid not in by_seg:
            by_seg[sid] = {
                "name": e["segment_name"],
                "distance_m": e["segment_distance_m"],
                "times": [],
                "ranks": [],
            }
        if e["elapsed_time_s"] is not None:
            by_seg[sid]["times"].append(e["elapsed_time_s"])
        if e["overall_rank"] is not None:
            by_seg[sid]["ranks"].append(e["overall_rank"])

    def _trend_pct(times: list[int]) -> float | None:
        if len(times) < 2:
            return None
        half = len(times) // 2
        first_avg = sum(times[:half]) / half
        last_avg = sum(times[half:]) / (len(times) - half)
        if first_avg == 0:
            return None
        return round((last_avg - first_avg) / first_avg * 100, 1)

    def _fmt_date(iso: str) -> str:
        try:
            d = date.fromisoformat(iso[:10])
            return f"{d.day:02d}.{d.month:02d}."
        except Exception:
            return iso[:10]

    koms = [
        {
            "segment_id": r["segment_id"],
            "segment_name": r["segment_name"],
            "distance_m": r["segment_distance_m"],
            "elapsed_time_s": r["elapsed_time_s"],
            "activity_date": _fmt_date(r["activity_date"] or ""),
            "overall_rank": 1,
        }
        for r in koms_raw
    ]

    opportunities = []
    for sid, data in by_seg.items():
        if sid in kom_ids:
            continue
        times = data["times"]
        min_rank = min(data["ranks"]) if data["ranks"] else None
        trend_pct = _trend_pct(times)
        is_near = min_rank in (2, 3)
        is_trending = trend_pct is not None and trend_pct < -3.0

        if is_near or is_trending:
            opportunities.append({
                "segment_id": sid,
                "segment_name": data["name"],
                "distance_m": data["distance_m"],
                "elapsed_time_s": min(times) if times else None,
                "overall_rank": min_rank,
                "trend_pct": trend_pct,
                "is_trending": is_trending,
            })

    opportunities.sort(key=lambda x: (x["overall_rank"] or 99, x["trend_pct"] or 0))
    return {"koms": koms, "opportunities": opportunities}
```

- [ ] **Step 4: Run all dashboard tests**

```
uv run pytest tests/test_dashboard.py -v
```
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add dashboard.py tests/test_dashboard.py
git commit -m "feat: add /api/segments endpoint with KOM and opportunity logic"
```

---

## Task 4: UI Layer — Segmente tab

**Files:**
- Modify: `dashboard.py` (only the HTML in `_dashboard_html()`)

- [ ] **Step 1: Add tab bar**

In `_dashboard_html()`, find this line (just before `<main ...>`):
```html
  <main class="max-w-3xl mx-auto px-4 py-6 space-y-8">
```

Replace it with:
```html
  <!-- Tab bar -->
  <nav class="border-b border-border px-4">
    <div class="max-w-3xl mx-auto flex">
      <button id="tab-dashboard" onclick="switchTab('dashboard')"
        class="px-4 py-3 text-sm font-semibold border-b-2 border-lime text-lime transition-colors">
        Dashboard
      </button>
      <button id="tab-segmente" onclick="switchTab('segmente')"
        class="px-4 py-3 text-sm font-semibold border-b-2 border-transparent text-muted hover:text-on-surface transition-colors">
        Segmente
      </button>
    </div>
  </nav>

  <main class="max-w-3xl mx-auto px-4 py-6 space-y-8">
```

- [ ] **Step 2: Wrap existing sections in a dashboard view div**

Wrap the three existing `<section>` elements (Wochenzusammenfassung, Letzte Einheiten, Trends) in:
```html
<div id="view-dashboard">
  <!-- existing sections here -->
</div>
```

- [ ] **Step 3: Add segments tab content**

Directly after `</div>` (closing the dashboard view), before `</main>`, add:
```html
    <!-- Segments Tab -->
    <div id="view-segmente" class="hidden space-y-6">
      <section>
        <h2 class="text-base font-semibold text-on-surface mb-3">Meine Rekorde</h2>
        <div id="seg-koms"><p class="text-sm text-muted">Wird geladen…</p></div>
      </section>
      <section>
        <h2 class="text-base font-semibold text-on-surface mb-3">Rekord-Chancen</h2>
        <p class="text-xs text-muted mb-3">Segmente wo du auf Platz 2–3 bist oder schneller wirst.</p>
        <div id="seg-opps"><p class="text-sm text-muted">Wird geladen…</p></div>
      </section>
    </div>
```

- [ ] **Step 4: Add JS for tab switching and segment rendering**

In the `<script>` block, add before the closing `</script>` tag:
```javascript
    function fmtTime(s) {
      if (s == null) return '—';
      const m = Math.floor(s / 60), sec = s % 60;
      return `${m}:${String(sec).padStart(2, '0')}`;
    }

    function fmtDist(m) {
      return m != null ? (m / 1000).toFixed(1) + ' km' : '—';
    }

    function segTable(rows, cols) {
      if (!rows.length) return '<p class="text-sm text-muted">Keine Daten.</p>';
      const head = cols.map(c =>
        `<th class="text-left text-xs font-bold uppercase tracking-wider text-muted px-3 py-2">${c.label}</th>`
      ).join('');
      const body = rows.map(r =>
        `<tr class="border-t border-border hover:bg-surface-high transition-colors">
          ${cols.map(c => `<td class="px-3 py-2 text-sm text-on-surface">${c.render(r)}</td>`).join('')}
        </tr>`
      ).join('');
      return `<table class="w-full bg-surface-low border border-border rounded-lg overflow-hidden">
        <thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
    }

    let _segLoaded = false;

    async function loadSegments() {
      if (_segLoaded) return;
      _segLoaded = true;
      try {
        const r = await fetch('/api/segments');
        const d = await r.json();

        if (!d.koms.length && !d.opportunities.length) {
          document.getElementById('seg-koms').innerHTML =
            '<p class="text-sm text-muted">Keine Segment-Daten. Starte <code class="bg-surface px-1 rounded">uv run sync.py</code> um Daten zu laden.</p>';
          document.getElementById('seg-opps').innerHTML = '';
          return;
        }

        document.getElementById('seg-koms').innerHTML = segTable(d.koms, [
          { label: 'Segment',     render: r => escapeHtml(r.segment_name) },
          { label: 'Distanz',     render: r => fmtDist(r.distance_m) },
          { label: 'Beste Zeit',  render: r => fmtTime(r.elapsed_time_s) },
          { label: 'Datum',       render: r => escapeHtml(r.activity_date || '—') },
        ]);

        document.getElementById('seg-opps').innerHTML = segTable(d.opportunities, [
          { label: 'Segment',     render: r => escapeHtml(r.segment_name) },
          { label: 'Distanz',     render: r => fmtDist(r.distance_m) },
          { label: 'Meine Zeit',  render: r => fmtTime(r.elapsed_time_s) },
          { label: 'Platz',       render: r => r.overall_rank ? `#${r.overall_rank}` : '—' },
          { label: 'Trend',       render: r => r.is_trending
              ? `<span class="text-lime font-semibold">↑ ${Math.abs(r.trend_pct)}%</span>`
              : '<span class="text-muted">—</span>' },
        ]);
      } catch (e) {
        console.error('Segment load error:', e);
      }
    }

    function switchTab(tab) {
      const isDash = tab === 'dashboard';
      document.getElementById('view-dashboard').classList.toggle('hidden', !isDash);
      document.getElementById('view-segmente').classList.toggle('hidden', isDash);
      const active = 'border-lime text-lime';
      const inactive = 'border-transparent text-muted hover:text-on-surface';
      document.getElementById('tab-dashboard').className =
        `px-4 py-3 text-sm font-semibold border-b-2 ${isDash ? active : inactive} transition-colors`;
      document.getElementById('tab-segmente').className =
        `px-4 py-3 text-sm font-semibold border-b-2 ${isDash ? inactive : active} transition-colors`;
      if (!isDash) loadSegments();
    }
```

- [ ] **Step 5: Run full test suite**

```
uv run pytest tests/ -v
```
Expected: ALL PASS (UI changes don't break existing tests)

- [ ] **Step 6: Start server and verify in browser**

```
cd /home/user/Dokumente/strava-mcp
uv run python dashboard.py
```
Open http://localhost:8080 and verify:
- "Segmente" tab appears next to "Dashboard" in the nav
- Clicking "Segmente" shows both sections without page reload
- "Dashboard" tab still works, sport/weeks filters still work
- With no segment data: shows the "run sync.py" message

- [ ] **Step 7: Commit**

```bash
git add dashboard.py
git commit -m "feat: add Segmente tab UI with KOMs and record opportunity tables"
```
