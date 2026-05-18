# Activity Detail Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a slide-in detail panel that opens when clicking an activity card, showing all stats, a Leaflet route map, and a longer AI analysis comment.

**Architecture:** Two new FastAPI endpoints (`/api/activity/{id}` and `/api/activity/{id}/detail-comment`) feed a vanilla-JS slide-in panel. Route is rendered via Leaflet.js by decoding the `summary_polyline` already stored in `raw_json`. The longer AI comment is generated on-demand by a new `generate_detail_comment` function in `lib/ai_coach.py` and is NOT cached (no extra DB column).

**Tech Stack:** Python 3.12, FastAPI, SQLite, `anthropic` SDK (Haiku), Leaflet.js + OpenStreetMap via CDN, vanilla JS, pytest + FastAPI TestClient.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `dashboard.py` | Modify | Add 2 new API endpoints; extend `_dashboard_html()` with slide-in panel HTML/JS |
| `lib/ai_coach.py` | Modify | Add `generate_detail_comment` function (longer prompt, 250 tokens) |
| `tests/test_dashboard.py` | Modify | Add tests for new API endpoints |
| `tests/test_ai_coach.py` | Modify | Add test for `generate_detail_comment` |

---

## Context for implementers

- Project root: `/home/user/Dokumente/strava-mcp`
- Run tests: `cd /home/user/Dokumente/strava-mcp && uv run pytest`
- `lib/db.py` — `get_conn`, `init_db`, `get_activities`; the `activities` table has: `id, name, sport_type, start_date_utc, start_date_local, distance_m, moving_time_s, elevation_gain_m, avg_speed_ms, max_speed_ms, avg_heartrate, kilojoules, ai_comment, raw_json`
- `raw_json` contains the full Strava API response including `map.summary_polyline`, `elev_high`, `elev_low`, `average_watts`, `elapsed_time`, `pr_count`, `max_heartrate`
- `lib/grade.py` — `compute_grade(activity: dict, all_activities: list[dict]) -> str`
- `lib/ai_coach.py` — `generate_comment(activity, grade, client)` (1–2 sentences, 80 tokens); `_get_api_key()` reads from `.env`; `MODEL = "claude-haiku-4-5-20251001"`
- `dashboard.py` — FastAPI app with `_eas_kmh(row)` helper; imports `from lib.db import get_conn, init_db, get_activities` and `from lib.grade import compute_grade`
- `tests/conftest.py` — `db` fixture (in-memory SQLite, `check_same_thread=False`); `db_with_many_activities` (10 GravelRide activities)
- `tests/test_dashboard.py` — imports `import dashboard` at module level; uses `patch("dashboard.get_conn", return_value=db_fixture)` inside `with` blocks
- All 45 tests currently pass

---

## Task 1: Backend — `/api/activity/{id}` endpoint

**Files:**
- Modify: `dashboard.py` — add new endpoint
- Modify: `tests/test_dashboard.py` — add 3 tests

### Step 1: Write the failing tests

Add to `tests/test_dashboard.py`:

```python
def test_activity_detail_returns_404_for_unknown(db_with_many_activities):
    with patch("dashboard.get_conn", return_value=db_with_many_activities):
        client = TestClient(dashboard.app)
        resp = client.get("/api/activity/9999")
    assert resp.status_code == 404


def test_activity_detail_shape(db_with_many_activities):
    with patch("dashboard.get_conn", return_value=db_with_many_activities):
        client = TestClient(dashboard.app)
        resp = client.get("/api/activity/5")
    assert resp.status_code == 200
    d = resp.json()
    for field in ("id", "name", "sport_type", "date", "distance_km", "duration_min",
                  "elapsed_min", "avg_speed_kmh", "eas_kmh", "max_speed_kmh",
                  "elevation_gain_m", "elev_high_m", "elev_low_m",
                  "avg_heartrate", "avg_watts", "kilojoules", "pr_count",
                  "grade", "ai_comment", "summary_polyline"):
        assert field in d, f"Missing field: {field}"


def test_activity_detail_grade_is_valid(db_with_many_activities):
    with patch("dashboard.get_conn", return_value=db_with_many_activities):
        client = TestClient(dashboard.app)
        resp = client.get("/api/activity/3")
    assert resp.json()["grade"] in ("A+", "A", "B+", "B", "C")
```

### Step 2: Run tests to verify they fail

```bash
cd /home/user/Dokumente/strava-mcp && uv run pytest tests/test_dashboard.py::test_activity_detail_shape tests/test_dashboard.py::test_activity_detail_returns_404_for_unknown -v
```

Expected: FAIL — 404 endpoint does not exist yet.

### Step 3: Add the endpoint to `dashboard.py`

Add this import at the top of `dashboard.py` (add `json` to imports):
```python
import json
```

Add this import alongside the existing lib imports:
```python
from fastapi import FastAPI, HTTPException
```

Add the endpoint after the `api_data` function:

```python
@app.get("/api/activity/{activity_id}")
def api_activity_detail(activity_id: int):
    conn = get_conn()
    init_db(conn)
    row = conn.execute(
        "SELECT * FROM activities WHERE id = ?", (activity_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Activity not found")

    r = dict(row)
    raw = json.loads(r.get("raw_json") or "{}")

    # Compute grade against same-sport peers
    sport = r.get("sport_type")
    peers = [dict(a) for a in get_activities(conn, sport_type=sport)]
    grade = compute_grade(r, peers)

    start = (r.get("start_date_local") or "")[:10]
    try:
        d = date.fromisoformat(start)
        date_str = f"{d.day:02d}.{d.month:02d}."
    except Exception:
        date_str = start

    avg_watts = raw.get("average_watts")
    max_spd_ms = r.get("max_speed_ms") or 0

    return {
        "id": r["id"],
        "name": r.get("name") or "",
        "sport_type": r.get("sport_type") or "",
        "date": date_str,
        "start_date_local": r.get("start_date_local") or "",
        "distance_km": round((r.get("distance_m") or 0) / 1000, 1),
        "duration_min": round((r.get("moving_time_s") or 0) / 60),
        "elapsed_min": round((raw.get("elapsed_time") or r.get("moving_time_s") or 0) / 60),
        "avg_speed_kmh": round((r.get("avg_speed_ms") or 0) * 3.6, 1),
        "eas_kmh": round(_eas_kmh(r), 1),
        "max_speed_kmh": round(max_spd_ms * 3.6, 1),
        "elevation_gain_m": round(r["elevation_gain_m"]) if r.get("elevation_gain_m") is not None else None,
        "elev_high_m": raw.get("elev_high"),
        "elev_low_m": raw.get("elev_low"),
        "avg_heartrate": r.get("avg_heartrate"),
        "max_heartrate": raw.get("max_heartrate"),
        "avg_watts": round(avg_watts) if avg_watts else None,
        "kilojoules": round(r["kilojoules"]) if r.get("kilojoules") is not None else None,
        "pr_count": raw.get("pr_count") or 0,
        "grade": grade,
        "ai_comment": r.get("ai_comment"),
        "summary_polyline": (raw.get("map") or {}).get("summary_polyline"),
    }
```

### Step 4: Run tests to verify they pass

```bash
uv run pytest tests/test_dashboard.py -v
```

Expected: all PASS (new 3 + existing 7 = 10 total).

### Step 5: Run full suite

```bash
uv run pytest -v
```

Expected: all 48 PASS.

### Step 6: Commit

```bash
git add dashboard.py tests/test_dashboard.py
git commit -m "feat: /api/activity/{id} detail endpoint"
```

---

## Task 2: `generate_detail_comment` in `lib/ai_coach.py`

**Files:**
- Modify: `lib/ai_coach.py` — add `generate_detail_comment` function
- Modify: `tests/test_ai_coach.py` — add 2 tests

The detail comment is longer (3–4 sentences, 250 tokens), uses more stats (max speed, elevation profile, watts, PRs), and is generated on-demand by the frontend via `/api/activity/{id}/detail-comment`.

### Step 1: Write the failing tests

Add to `tests/test_ai_coach.py`:

```python
def test_generate_detail_comment_returns_string():
    from lib.ai_coach import generate_detail_comment
    row = {
        "id": 1, "name": "Seen Runde", "sport_type": "GravelRide",
        "distance_m": 75300.0, "moving_time_s": 11580,
        "elevation_gain_m": 417.0, "avg_speed_ms": 6.503,
        "max_speed_ms": 18.0, "avg_heartrate": None,
        "kilojoules": 1376.0, "elev_high_m": 182.0, "elev_low_m": 73.0,
        "avg_watts": None, "pr_count": 0,
    }
    client = _mock_client("Hervorragende Fahrt! Du hast eine exzellente Pace gehalten.")
    result = generate_detail_comment(row, "A+", client)
    assert isinstance(result, str)
    assert len(result) > 10


def test_generate_detail_comment_returns_none_on_error():
    from lib.ai_coach import generate_detail_comment
    row = {"id": 1, "name": "Test", "sport_type": "GravelRide",
           "distance_m": 40000, "moving_time_s": 6000,
           "elevation_gain_m": 200, "avg_speed_ms": 6.5,
           "max_speed_ms": 15.0, "avg_heartrate": None,
           "kilojoules": 500, "elev_high_m": None, "elev_low_m": None,
           "avg_watts": None, "pr_count": 0}
    client = _mock_client()
    client.messages.create.side_effect = Exception("fail")
    result = generate_detail_comment(row, "B", client)
    assert result is None
```

### Step 2: Run tests to verify they fail

```bash
uv run pytest tests/test_ai_coach.py::test_generate_detail_comment_returns_string tests/test_ai_coach.py::test_generate_detail_comment_returns_none_on_error -v
```

Expected: FAIL — `generate_detail_comment` does not exist.

### Step 3: Implement `generate_detail_comment` in `lib/ai_coach.py`

Add constant near the top (below `MAX_TOKENS = 80`):
```python
MAX_TOKENS_DETAIL = 250
```

Add function after `generate_comment`:

```python
def generate_detail_comment(activity: dict, grade: str, client: anthropic.Anthropic) -> str | None:
    dist_km = (activity.get("distance_m") or 0) / 1000
    duration_min = (activity.get("moving_time_s") or 0) / 60
    speed_kmh = (activity.get("avg_speed_ms") or 0) * 3.6
    max_speed_kmh = (activity.get("max_speed_ms") or 0) * 3.6
    elev = activity.get("elevation_gain_m") or 0
    elev_high = activity.get("elev_high_m")
    elev_low = activity.get("elev_low_m")
    watts = activity.get("avg_watts")
    heartrate = activity.get("avg_heartrate")
    pr_count = activity.get("pr_count") or 0

    lines = [
        f"Du bist ein erfahrener Trainer. Analysiere diese Einheit in 3–4 Sätzen auf Deutsch (Du-Form). "
        f"Gib eine konkrete Einordnung: Was lief gut? Was könnte besser sein? Gibt es einen Trainingshinweis?\n",
        f"Sport: {activity.get('sport_type', 'Unbekannt')}",
        f"Name: {activity.get('name', '')}",
        f"Note: {grade}",
        f"Distanz: {dist_km:.1f} km",
        f"Dauer: {duration_min:.0f} min",
        f"Ø Tempo: {speed_kmh:.1f} km/h  |  Max: {max_speed_kmh:.1f} km/h",
        f"Höhenmeter: {elev:.0f} m",
    ]
    if elev_high is not None and elev_low is not None:
        lines.append(f"Höhenprofil: {elev_low:.0f}–{elev_high:.0f} m ü.NN")
    if heartrate:
        lines.append(f"Ø Herzfrequenz: {heartrate:.0f} bpm")
    if watts:
        lines.append(f"Ø Leistung: {watts:.0f} W  |  {(watts * duration_min * 60 / 1000):.0f} kJ")
    if pr_count:
        lines.append(f"Strava-PRs: {pr_count}")
    lines.append("\nNur die Analyse, keine Einleitung.")

    prompt = "\n".join(lines)

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS_DETAIL,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()
    except Exception as e:
        logger.warning("Detail comment generation failed for activity %s: %s", activity.get("id"), e)
        return None
```

### Step 4: Write the failing test for the detail-comment endpoint

Add to `tests/test_dashboard.py`:

```python
def test_activity_detail_comment_returns_json(db_with_many_activities):
    from unittest.mock import MagicMock
    mock_client = MagicMock()
    mock_client.messages.create.return_value.content = [MagicMock(text="Klasse Fahrt!")]
    with patch("dashboard.get_conn", return_value=db_with_many_activities), \
         patch("dashboard._get_api_key", return_value="test-key"), \
         patch("dashboard._anthropic.Anthropic", return_value=mock_client):
        client = TestClient(dashboard.app)
        resp = client.get("/api/activity/5/detail-comment")
    assert resp.status_code == 200
    assert "comment" in resp.json()
```

### Step 5: Run test to verify it fails

```bash
uv run pytest tests/test_dashboard.py::test_activity_detail_comment_returns_json -v
```

Expected: FAIL — endpoint does not exist yet.

### Step 6: Add `/api/activity/{id}/detail-comment` endpoint to `dashboard.py`

Add this import at the top of `dashboard.py` (alongside the existing lib imports):
```python
from lib.ai_coach import generate_detail_comment, _get_api_key
import anthropic as _anthropic
```

Add the endpoint after `api_activity_detail`:

```python
@app.get("/api/activity/{activity_id}/detail-comment")
def api_activity_detail_comment(activity_id: int):
    conn = get_conn()
    init_db(conn)
    row = conn.execute(
        "SELECT * FROM activities WHERE id = ?", (activity_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Activity not found")

    r = dict(row)
    raw = json.loads(r.get("raw_json") or "{}")

    sport = r.get("sport_type")
    peers = [dict(a) for a in get_activities(conn, sport_type=sport)]
    grade = compute_grade(r, peers)

    activity_for_comment = {
        **r,
        "elev_high_m": raw.get("elev_high"),
        "elev_low_m": raw.get("elev_low"),
        "avg_watts": raw.get("average_watts"),
        "pr_count": raw.get("pr_count") or 0,
    }

    api_key = _get_api_key()
    if not api_key:
        return {"comment": None, "error": "ANTHROPIC_API_KEY not configured"}

    client = _anthropic.Anthropic(api_key=api_key)
    comment = generate_detail_comment(activity_for_comment, grade, client)
    return {"comment": comment}
```

### Step 7: Run all tests

```bash
uv run pytest -v
```

Expected: all PASS (51 total).

### Step 8: Commit

```bash
git add lib/ai_coach.py dashboard.py tests/test_ai_coach.py tests/test_dashboard.py
git commit -m "feat: generate_detail_comment and /api/activity/{id}/detail-comment endpoint"
```

---

## Task 3: Frontend — slide-in panel with Leaflet map

**Files:**
- Modify: `dashboard.py` — extend `_dashboard_html()` with panel HTML and JS

No new tests needed — visual changes. The `test_root_has_tailwind` test already verifies HTML is returned. Run all tests after to confirm no regressions.

### Overview of changes to `_dashboard_html()`

**HTML additions (inside `<body>`, before `</body>`):**

1. A Leaflet CDN `<link>` + `<script>` in `<head>`
2. A slide-in panel `<aside id="detail-panel">` after `<main>`, hidden initially
3. An overlay `<div id="panel-overlay">` for click-to-close

**JS additions (inside `<script>`):**

1. `openDetail(id)` — fetches `/api/activity/{id}`, populates panel, slides it in
2. `loadDetailComment(id)` — fetches `/api/activity/{id}/detail-comment`, replaces spinner with text
3. `initMap(polyline)` — decodes Google-encoded polyline and renders Leaflet map
4. `closePanel()` — slides panel out
5. Update `activityCard()` to add `onclick="openDetail(${act.id})"` and `cursor-pointer`

### Step 1: Add Leaflet CDN to `<head>` in `_dashboard_html()`

Find the existing `<head>` block and add after the Material Symbols link:

```html
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
```

### Step 2: Add panel HTML after `</main>`

Add before `</body>`:

```html
  <!-- Overlay -->
  <div id="panel-overlay" class="fixed inset-0 bg-black/40 z-20 hidden" onclick="closePanel()"></div>

  <!-- Detail Panel -->
  <aside id="detail-panel"
    class="fixed top-0 right-0 h-full w-full sm:w-[420px] bg-surface border-l border-border z-30
           transform translate-x-full transition-transform duration-300 ease-out overflow-y-auto">

    <!-- Panel Header -->
    <div class="sticky top-0 bg-surface border-b border-border px-4 py-3 flex items-center gap-3">
      <button onclick="closePanel()" class="text-muted hover:text-on-surface transition-colors">
        <span class="material-symbols-outlined text-xl">arrow_back</span>
      </button>
      <div class="flex-1 min-w-0">
        <p id="panel-name" class="text-sm font-semibold text-on-surface truncate"></p>
        <p id="panel-meta" class="text-xs text-muted"></p>
      </div>
      <div id="panel-grade" class="w-9 h-9 rounded-lg flex items-center justify-center text-xs font-bold shrink-0"></div>
    </div>

    <!-- Stats Grid -->
    <div class="px-4 py-4">
      <h3 class="text-xs font-bold uppercase tracking-wider text-muted mb-3">Stats</h3>
      <div id="panel-stats" class="grid grid-cols-2 gap-2"></div>
    </div>

    <!-- Route Map -->
    <div class="px-4 pb-4">
      <h3 class="text-xs font-bold uppercase tracking-wider text-muted mb-3">Route</h3>
      <div id="panel-map" class="rounded-lg overflow-hidden h-48 bg-surface-low border border-border
                                  flex items-center justify-center text-muted text-xs">
        <span>Keine Route verfügbar</span>
      </div>
    </div>

    <!-- AI Analysis -->
    <div class="px-4 pb-8">
      <h3 class="text-xs font-bold uppercase tracking-wider text-muted mb-3">KI-Analyse</h3>
      <div id="panel-ai" class="text-sm text-muted italic leading-relaxed">
        <span class="animate-pulse">Analyse wird geladen…</span>
      </div>
    </div>
  </aside>
```

### Step 3: Add JS functions inside `<script>` (before closing `</script>`)

Add after the existing `load()` function:

```javascript
    // ---- Polyline decoder (Google Encoded Polyline Algorithm) ----
    function decodePolyline(encoded) {
      const coords = [];
      let idx = 0, lat = 0, lng = 0;
      while (idx < encoded.length) {
        let b, shift = 0, result = 0;
        do { b = encoded.charCodeAt(idx++) - 63; result |= (b & 0x1f) << shift; shift += 5; } while (b >= 0x20);
        lat += (result & 1) ? ~(result >> 1) : (result >> 1);
        shift = 0; result = 0;
        do { b = encoded.charCodeAt(idx++) - 63; result |= (b & 0x1f) << shift; shift += 5; } while (b >= 0x20);
        lng += (result & 1) ? ~(result >> 1) : (result >> 1);
        coords.push([lat / 1e5, lng / 1e5]);
      }
      return coords;
    }

    let _leafletMap = null;

    function initMap(polyline) {
      const container = document.getElementById('panel-map');
      if (!polyline) {
        container.innerHTML = '<span class="text-xs text-muted">Keine Route verfügbar</span>';
        return;
      }
      container.innerHTML = '';
      if (_leafletMap) { _leafletMap.remove(); _leafletMap = null; }
      const coords = decodePolyline(polyline);
      _leafletMap = L.map(container, { zoomControl: false, attributionControl: false });
      L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png').addTo(_leafletMap);
      const line = L.polyline(coords, { color: '#abd600', weight: 3 }).addTo(_leafletMap);
      _leafletMap.fitBounds(line.getBounds(), { padding: [8, 8] });
    }

    function statCard(label, value, unit='') {
      if (value === null || value === undefined) return '';
      return `<div class="bg-surface-low border border-border rounded-lg p-3">
        <p class="text-xs text-muted mb-1">${label}</p>
        <p class="text-base font-bold text-on-surface">${value}<span class="text-xs font-normal text-muted ml-1">${unit}</span></p>
      </div>`;
    }

    async function loadDetailComment(id) {
      const el = document.getElementById('panel-ai');
      try {
        const r = await fetch(`/api/activity/${id}/detail-comment`);
        const d = await r.json();
        el.textContent = d.comment || 'Keine Analyse verfügbar.';
      } catch {
        el.textContent = 'Analyse konnte nicht geladen werden.';
      }
    }

    async function openDetail(id) {
      const panel = document.getElementById('detail-panel');
      const overlay = document.getElementById('panel-overlay');

      // Reset panel state
      document.getElementById('panel-name').textContent = '…';
      document.getElementById('panel-meta').textContent = '';
      document.getElementById('panel-grade').textContent = '';
      document.getElementById('panel-grade').className = 'w-9 h-9 rounded-lg flex items-center justify-center text-xs font-bold shrink-0';
      document.getElementById('panel-stats').innerHTML = '';
      document.getElementById('panel-map').innerHTML = '<span class="text-xs text-muted">Lädt…</span>';
      document.getElementById('panel-ai').innerHTML = '<span class="animate-pulse">Analyse wird geladen…</span>';

      // Slide in
      overlay.classList.remove('hidden');
      requestAnimationFrame(() => panel.classList.remove('translate-x-full'));

      try {
        const r = await fetch(`/api/activity/${id}`);
        if (!r.ok) return;
        const d = await r.json();

        document.getElementById('panel-name').textContent = d.name;
        document.getElementById('panel-meta').textContent =
          `${d.date} · ${d.sport_type} · Note ${d.grade}`;

        const gradeEl = document.getElementById('panel-grade');
        gradeEl.textContent = d.grade;
        gradeEl.className = `w-9 h-9 rounded-lg flex items-center justify-center text-xs font-bold shrink-0 ${GRADE_CLASS[d.grade] || 'grade-c'}`;

        document.getElementById('panel-stats').innerHTML = [
          statCard('Distanz', d.distance_km, 'km'),
          statCard('Dauer', d.duration_min, 'min'),
          statCard('Ø Tempo', d.avg_speed_kmh, 'km/h'),
          statCard('Tempo EAS', d.eas_kmh, 'km/h'),
          statCard('Max-Tempo', d.max_speed_kmh, 'km/h'),
          statCard('Höhenmeter', d.elevation_gain_m, 'm'),
          statCard('Höchster Punkt', d.elev_high_m ? Math.round(d.elev_high_m) : null, 'm'),
          statCard('Tiefster Punkt', d.elev_low_m ? Math.round(d.elev_low_m) : null, 'm'),
          statCard('Herzfrequenz Ø', d.avg_heartrate ? Math.round(d.avg_heartrate) : null, 'bpm'),
          statCard('Max Herzfrequenz', d.max_heartrate, 'bpm'),
          statCard('Leistung Ø', d.avg_watts, 'W'),
          statCard('Energie', d.kilojoules, 'kJ'),
          statCard('Strava PRs', d.pr_count || null, ''),
        ].join('');

        initMap(d.summary_polyline);
        loadDetailComment(id);
      } catch (e) {
        console.error(e);
      }
    }

    function closePanel() {
      const panel = document.getElementById('detail-panel');
      const overlay = document.getElementById('panel-overlay');
      panel.classList.add('translate-x-full');
      overlay.classList.add('hidden');
      if (_leafletMap) { _leafletMap.remove(); _leafletMap = null; }
    }
```

### Step 4: Make activity cards clickable

In the existing `activityCard(act)` function, change the outer `<div>` to add `cursor-pointer` and `onclick`:

Find:
```javascript
        <div class="bg-surface-low border border-border rounded-lg p-3 flex items-start gap-3 hover:border-lime/30 transition-colors">
```

Replace with:
```javascript
        <div class="bg-surface-low border border-border rounded-lg p-3 flex items-start gap-3 hover:border-lime/30 transition-colors cursor-pointer" onclick="openDetail(${act.id})">
```

### Step 5: Run all tests

```bash
cd /home/user/Dokumente/strava-mcp && uv run pytest -v
```

Expected: all 51 PASS (HTML changes don't break existing tests).

### Step 6: Commit

```bash
git add dashboard.py
git commit -m "feat: slide-in activity detail panel with Leaflet route map and AI analysis"
```

---

## Final check

```bash
uv run pytest -v
uv run dashboard.py
```

Open http://localhost:8080, click any activity → panel slides in with stats, map, AI analysis.
