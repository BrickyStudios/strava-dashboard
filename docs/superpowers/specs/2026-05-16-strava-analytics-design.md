# Strava Analytics & Performance Tracking — Design Spec

**Date:** 2026-05-16  
**Project:** strava-mcp  
**Status:** Approved

---

## Problem

The existing `strava-mcp` can only fetch the last 200 activities via live API calls — no historical trend analysis, no offline access, no visual performance overview. During rides, the user has to stop and look at the phone map because Strava has no audio turn-by-turn navigation.

---

## Goals

1. **Primary:** Track cycling performance development over time (speed, volume, elevation-adjusted pace)
2. **Secondary (Phase 2):** Heart rate zone analysis using Gadgetbridge data
3. **Navigation fix:** Eliminate the need to stop and look at phone during rides

Out of scope: Gadgetbridge HR integration (Phase 2), mobile app, cloud sync.

---

## Architecture

```
┌─────────────────────────────────────────────────┐
│              SQLite Database                     │
│   ~/Dokumente/strava-mcp/data/activities.db     │
└────────────┬───────────────┬────────────────────┘
             │               │
    ┌────────▼──────┐  ┌─────▼──────────────────┐
    │   sync.py     │  │   main.py (MCP)         │
    │               │  │   + 3 analytics tools   │
    │  incremental  │  └─────────────────────────┘
    │  resumable    │             │
    └───────────────┘   ┌────────▼───────────┐
                        │   dashboard.py      │
                        │   FastAPI + Chart.js│
                        │   localhost:8080    │
                        └────────────────────┘
```

All components share the same SQLite database. No external services beyond Strava API.

---

## Data Model

### `activities` table

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | Strava Activity ID |
| `name` | TEXT | |
| `sport_type` | TEXT | Strava canonical value: `GravelRide`, `Run`, etc. |
| `start_date_utc` | TEXT | ISO 8601 UTC from Strava |
| `start_date_local` | TEXT | ISO 8601 local time from Strava (`start_date_local` field) |
| `distance_m` | REAL | meters |
| `moving_time_s` | INTEGER | seconds |
| `elevation_gain_m` | REAL | |
| `avg_speed_ms` | REAL | m/s |
| `max_speed_ms` | REAL | m/s |
| `avg_heartrate` | REAL | NULL if unavailable |
| `kilojoules` | REAL | |
| `raw_json` | TEXT | Full Strava response for future use |

**Why `start_date_local`?** Week bucketing uses local date (user is UTC+1/+2). A ride at 23:00 local on Sunday must not bucket into the wrong week. All weekly aggregations use `start_date_local`.

**Why `raw_json`?** Future fields (HR zones, segments, gear) can be extracted without re-syncing.

### `sync_state` table

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PRIMARY KEY CHECK (id = 1) | Enforces single row |
| `last_synced_epoch` | INTEGER | Unix epoch of last successful sync |
| `total_activities` | INTEGER | |

Use `INSERT OR REPLACE INTO sync_state` to update. The `CHECK (id = 1)` constraint guarantees at most one row.

**`last_synced_epoch` is INTEGER (Unix epoch)** — the Strava `after` parameter requires a Unix epoch integer, not ISO 8601.

---

## Components

### 1. `lib/db.py` — Database Layer

Thin wrapper around stdlib `sqlite3`:
- `init_db()` — creates tables if not exists
- `upsert_activity(activity_dict)` — INSERT OR REPLACE
- `get_activities(sport_type=None, since_local=None, until_local=None)` — filtered query; `sport_type=None` means all types
- `get_sync_state() -> dict | None`
- `set_sync_state(epoch: int, count: int)`

No ORM.

---

### 2. `sync.py` — Strava Sync Script

#### Token Refresh (NEW code — not a reuse)

`lib/api.py` currently reads tokens with `dotenv_values()` (read-only). Strava rotates the refresh token on every use. `sync.py` must:

1. POST to `https://www.strava.com/oauth/token` with `grant_type=refresh_token`
2. Write the new `STRAVA_ACCESS_TOKEN` **and** `STRAVA_REFRESH_TOKEN` back to `.env`
3. Proceed with the new access token

This is new code in `sync.py` — not delegated to `lib/api.py`.

#### Sync Logic

```
1. Refresh tokens → write both back to .env
2. Read last_synced_epoch from sync_state (None if first run)
3. Paginate Strava API:
   GET /athlete/activities?per_page=200&page=N&after=last_synced_epoch
   (after=0 for --full runs)
   Increment page until empty response
4. Upsert each activity into SQLite (INSERT OR REPLACE)
5. After all pages succeed: set_sync_state(epoch=unix_now(), count=SELECT COUNT(*) FROM activities)
```

`set_sync_state` is written **once at the end** of a successful complete sync. `last_synced_epoch` therefore always represents a clean completed boundary — not a mid-sync position.

#### Rate Limit Handling

Strava enforces **100 requests / 15 minutes** and **1000 requests / day**.

After every API response, read headers:
- `X-RateLimit-Limit`: e.g. `100,1000`
- `X-RateLimit-Usage`: e.g. `43,150`

When 15-min usage reaches ≥ 90, sleep until the next 15-minute window boundary, then continue. If the daily limit is reached, print a warning and exit without updating `sync_state` — the next run will retry from `last_synced_epoch` (incremental) or page 1 (full).

**Resumability model:**
- **Incremental sync** (`after=last_synced_epoch`): inherently safe. Because `upsert` handles duplicates and the filter is time-based, a retry after any interruption will re-fetch and re-upsert any partially written activities without loss or duplication.
- **`--full` sync**: not resumable — restarts from page 1. Acceptable because `--full` is a one-time initial setup operation (or explicit user override). Upsert ensures no duplicates on re-run.

#### CLI

```bash
uv run sync.py          # incremental (default)
uv run sync.py --full   # re-fetch everything (resets last_synced_epoch to 0)
```

---

### 3. `main.py` — New MCP Analytics Tools

Three tools added alongside existing `fetch_activity` / `fetch_activities`. All read **only from SQLite** — fast, offline-capable, no rate limit concerns.

#### Sport type canonicalization

All tools accept `sport_type` as a Strava canonical string (`GravelRide`, `Run`, `Ride`, etc.) or `None` for all types. The DB stores the canonical Strava value. The dashboard UI uses human labels and converts before calling: `"Gravel" → "GravelRide"`, `"Run" → "Run"`, `"All" → None`. The string `"all"` is never passed to the MCP tools — only `None` or a canonical sport type.

---

#### `get_performance_trend`

```python
def get_performance_trend(
    sport_type: str | None,           # e.g. "GravelRide" or "all"
    metric: Literal["avg_speed_kmh", "distance_km", "elevation_m"],
    weeks: int = 12,                  # how many trailing weeks to return
) -> str:
```

Returns a formatted table of ISO-week averages (Monday–Sunday, bucketed by `start_date_local`):

```
Week        Avg Speed (km/h)
2026-W17    23.1
2026-W18    21.4
...
```

Weeks with no activities are included as `—` (not skipped), so gaps are visible.

**Averaging method per metric:**
- `avg_speed_kmh`: distance-weighted — `sum(distance_m) / sum(moving_time_s) * 3.6`. Matches Chart 1 exactly.
- `distance_km`: per-week sum of `distance_m / 1000`. Matches `get_weekly_volume`.
- `elevation_m`: per-week sum of `elevation_gain_m`. Matches `get_weekly_volume`.

---

#### `get_weekly_volume`

```python
def get_weekly_volume(
    sport_type: str | None,
    weeks: int = 12,
) -> str:
```

Returns per ISO-week totals (bucketed by `start_date_local`):

```
Week        km      Elevation (m)
2026-W17    75.3    417
2026-W18    48.9    213
...
```

---

#### `get_personal_bests`

```python
def get_personal_bests(
    sport_type: str | None,
) -> str:
```

Returns:
- `longest_distance_km` — single activity with highest `distance_m`
- `longest_duration_min` — single activity with highest `moving_time_s` (may differ from longest distance)
- `fastest_avg_speed_kmh` — single activity with highest `avg_speed_ms`
- `most_elevation_m` — single activity with highest `elevation_gain_m`

---

#### Return format

All three tools return a **formatted string** (same convention as existing tools) for Claude to narrate. They do not return structured dicts — dashboard reads directly from SQLite.

---

### 4. `dashboard.py` — Local Web Dashboard

FastAPI app at `localhost:8080`. Opens browser automatically on start.

#### Empty state

If the `activities` table has zero rows, render a full-page banner instead of charts:

```
No data yet. Run:  uv run sync.py
```

No Chart.js is loaded in this state to avoid empty-dataset errors.

#### Three charts (Chart.js)

All three charts share the same **sport type** and **time range** controls:

| Control | Values |
|---|---|
| Sport | Gravel (`GravelRide`), Run (`Run`), All (no filter) |
| Zeitraum | 4 Wochen, 12 Wochen, 26 Wochen, 52 Wochen |

Weeks are ISO weeks (Monday start), bucketed by `start_date_local`.

**Chart 1 — Ø Tempo pro Woche** (line)  
Y-axis: `avg_speed_kmh` (km/h). Per week: distance-weighted mean of all activity speeds. Formula: `sum(distance_m) / sum(moving_time_s) * 3.6`. Empty weeks shown as gap (Chart.js `spanGaps: false`).

**Chart 2 — km / Woche** (bar)  
Y-axis: `sum(distance_m) / 1000` per week. Straightforward sum.

**Chart 3 — Elevation Adjusted Speed** (line)  
Normalizes speed for elevation so hilly and flat rides are comparable.

Per activity: `eas_kmh = avg_speed_kmh + (elevation_gain_m / (distance_m / 1000)) * 0.04`  
(100 hm/km ≈ +4 km/h equivalent effort — simplified Minetti)

Per week: recompute from **summed weekly totals** (not mean of per-activity EAS):
```
weekly_avg_speed = sum(distance_m) / sum(moving_time_s) * 3.6
weekly_hm_per_km = sum(elevation_gain_m) / (sum(distance_m) / 1000)
weekly_eas = weekly_avg_speed + weekly_hm_per_km * 0.04
```

This keeps Chart 3 consistent with Charts 1 and 2 (same distance/time totals as base).

#### Start

```bash
uv run dashboard.py
```

No persistent server — run on demand.

---

## Navigation Fix (No Build Required)

Install **Komoot** on phone. Import the GPX route. Komoot provides audio turn-by-turn cues while Strava records GPS in parallel. Zero custom code.

---

## File Structure After Implementation

```
strava-mcp/
├── lib/
│   ├── api.py          (existing, unchanged)
│   ├── helpers.py      (existing, unchanged)
│   └── db.py           (NEW)
├── data/
│   └── activities.db   (NEW, gitignored)
├── main.py             (extended with 3 new tools)
├── sync.py             (NEW)
├── dashboard.py        (NEW)
├── pyproject.toml      (add: fastapi, uvicorn)
├── .gitignore          (add: data/)
└── .env
```

---

## Dependencies to Add

```toml
fastapi
uvicorn[standard]
```

All other dependencies (`httpx`, `mcp`, `python-dotenv`) already in `pyproject.toml`.

---

## Phase 2 Preview (Out of Scope Now)

- Gadgetbridge JSON → extract HR data → match by timestamp to Strava activities → populate `avg_heartrate`
- HR zone breakdown per activity
- Fitness & Freshness score (CTL/ATL/TSB model)
