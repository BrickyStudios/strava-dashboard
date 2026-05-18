# Dashboard Redesign & AI Coach Comments — Design Spec

**Date:** 2026-05-17  
**Project:** strava-mcp  
**Status:** Approved

---

## Problem

The existing dashboard shows raw charts (avg speed, km/week, EAS) but gives no interpretation. You can see *what happened*, not *what it means*. There is no sense of whether a ride was good, bad, or average relative to your own baseline, and no coaching insight per activity.

---

## Goals

1. **Redesign dashboard** using the Stitch-generated design system (Electric Lime, dark surfaces, Inter, Tailwind)
2. **Activity grading** — composite score (A+/A/B+/B/C) per activity based on distance, EAS, and elevation relative to personal median
3. **AI coach comments** — Claude Haiku generates a short German coaching sentence per activity, stored in SQLite, shown in dashboard

---

## Design System (from Stitch DESIGN.md)

| Token | Value |
|---|---|
| Background | `#131316` |
| Surface container | `#1f1f22` |
| Surface container low | `#1b1b1e` |
| Primary (Electric Lime) | `#abd600` / `#c3f400` |
| On-surface | `#e4e1e6` |
| On-surface-variant | `#c4c9ac` |
| Secondary (Orange) | `#ffb77d` |
| Font | Inter (Google Fonts) |
| Radius default | 4px; badges 8px |
| Spacing base | 8px grid |

Depth through tonal layers and 1px borders, no shadows.

---

## Layout

Single-page, no sidebar. Three sections stacked vertically:

```
┌─────────────────────────────────────┐
│  Header: "Gravel Dashboard"  [W20]  │
├───────────┬───────────┬─────────────┤
│ Total km  │ Avg km/h  │ Elevation m  │  ← Weekly Summary Cards (3 cols)
│ sparkline │ sparkline │ sparkline   │
├─────────────────────────────────────┤
│  This Week's Activities             │
│  ┌───────────────────────────[A+]─┐ │
│  │ Seen Runde · 75.3km · 3h 14m  │ │
│  │ "Deine längste Fahrt bisher…"  │ │
│  └────────────────────────────────┘ │
│  ┌───────────────────────────[B+]─┐ │
│  │ Rückweg · 14.0km · 33min      │ │
│  │ "Kurze Erholungsfahrt…"        │ │
│  └────────────────────────────────┘ │
├─────────────────────────────────────┤
│  4-Week Trends (2 sparkline cards)  │
│  Avg Speed ↑+5%  │  km/Woche       │
└─────────────────────────────────────┘
```

Week selector dropdown (4/12/26/52 Wochen) and sport type filter (Gravel/Run/Alle) in the header row.

---

## Components

### Weekly Summary Cards

Three cards side by side (responsive: stacked on mobile):
- **Total km** — sum of `distance_m / 1000` for the current ISO week
- **Avg km/h** — distance-weighted mean: `sum(distance_m) / sum(moving_time_s) * 3.6`
- **Elevation m** — sum of `elevation_gain_m`

Each card has a sparkline SVG (last 8 weeks of that metric) and a trend badge (e.g. `↑ +18% vs VW`) comparing to previous ISO week. The sparkline data is included in the `/api/data` response under `summary.sparklines` (see API section).

### Activity Feed

Displays the **10 most recent activities** (filtered by `sport_type` if set, regardless of the `weeks` selector — the feed always shows the latest rides). Each row:

```
[bike icon]  Name                            [A+]
             Date · km · duration
             "AI coach comment in italics"
```

Grade badge colors:
- A+ → Electric Lime background (`#c3f400`), dark text
- A  → Lime at 70% opacity
- B+ → Orange (`#f18400`)
- B  → Muted surface (`#2a2a2d`), on-surface text
- C  → Darker surface, on-surface-variant text

### 4-Week Trends

Two compact trend cards below the activity feed:
- **Avg Speed** (EAS, km/h) with % change vs 4 weeks ago
- **Distanz / Woche** (km) with % change

SVG sparklines, no axes, 2px stroke, Electric Lime for speed, Orange for distance.

---

## Elevation Adjusted Speed (EAS)

EAS normalises speed for elevation so hilly and flat rides are comparable:

```
eas_kmh = avg_speed_kmh + (elevation_gain_m / (distance_km)) * 0.04
```

100 hm/km ≈ +4 km/h equivalent effort (simplified Minetti). All "speed" metrics in the dashboard use EAS, not raw speed. The DB column `avg_speed_ms` is the raw Strava value; EAS is computed at query time.

---

## Grading Algorithm (`lib/grade.py`)

### Score computation

For each activity, compute a composite score 0–100:

```python
composite = 0.50 * dist_score + 0.30 * eas_score + 0.20 * elev_score
```

Each sub-score is a percentile rank within the same `sport_type` in the DB (all stored activities):

```python
# dist_score: what % of same-sport rides is this activity longer than? (higher = better)
dist_score = percentile_rank(activity.distance_m, all_distances) * 100
```

`percentile_rank(x, values)` returns the fraction of values strictly less than x — so the longest ride scores 100, the shortest scores 0.

**Cold start (< 5 activities of same sport):** fall back to absolute thresholds:

| Metric | A+ | A | B+ | B | C |
|---|---|---|---|---|---|
| Distance (GravelRide) | ≥60km | ≥40km | ≥25km | ≥10km | <10km |
| Distance (Run) | ≥15km | ≥10km | ≥7km | ≥5km | <5km |
| Distance (other) | ≥20km | ≥12km | ≥8km | ≥4km | <4km |

Cold-start grades are distance-only (EAS/elevation skipped until baseline exists).

### Score → Grade mapping

| Composite score | Grade |
|---|---|
| ≥ 85 | A+ |
| 70–84 | A |
| 55–69 | B+ |
| 40–54 | B |
| < 40 | C |

### Caching

Grade is computed on-the-fly at dashboard render time. Not stored in DB — it updates automatically as more rides are added and the percentile baseline grows.

---

## AI Coach Comments (`lib/ai_coach.py`)

### When generated

After each successful sync, `sync.py` calls `generate_missing_comments(conn)`. This queries:

```sql
SELECT * FROM activities WHERE ai_comment IS NULL ORDER BY start_date_local DESC LIMIT 20
```

For each activity without a comment, it calls Claude Haiku, stores the result, and moves on.

### DB change

Add column to `activities` table:

```sql
ALTER TABLE activities ADD COLUMN ai_comment TEXT;
```

Handled via `init_db()` — use `CREATE TABLE IF NOT EXISTS` with the column, plus a safe migration:

```python
# in init_db(), after CREATE TABLE:
conn.execute("ALTER TABLE activities ADD COLUMN ai_comment TEXT")
# wrapped in try/except OperationalError (column already exists)
```

### Prompt

```
Du bist ein erfahrener Radtrainer. Gib einen kurzen, motivierenden Kommentar 
(1–2 Sätze, Deutsch, Du-Form) zu dieser Einheit:

Sport: {sport_type}
Name: {name}
Distanz: {distance_km:.1f} km
Dauer: {duration_min:.0f} min
Durchschnittstempo: {avg_speed_kmh:.1f} km/h
Höhenmeter: {elevation_m:.0f} m
Note: {grade}

Nur der Kommentar, keine Einleitung.
```

Model: `claude-haiku-4-5-20251001`. Max tokens: 80.

### Grade snapshot in prompt

The grade passed to the prompt is computed at generation time and is a snapshot. Because grades shift as the baseline grows (more rides = recalibrated percentiles), the cached comment may eventually reference a grade that differs from the current computed grade. This drift is **accepted by design** — the comment reflects the ride as it felt at the time of sync. No re-generation on grade change.

### Failure handling

If the Haiku API call fails (network error, rate limit, etc.): log the error, leave `ai_comment` as `NULL`, and continue to the next activity. The dashboard renders no comment row when `ai_comment` is `NULL` — the card simply shows name + stats without the coaching line. The next sync will retry (the `WHERE ai_comment IS NULL` query picks it up again).

### Cost estimate

~150 input tokens + 60 output tokens per activity × Haiku pricing ≈ negligible. Generated once, cached forever.

---

## API Changes (`dashboard.py`)

### Endpoints

`GET /` — serves the full HTML page (Tailwind CDN + inline JS)

`GET /api/data?sport_type=&weeks=12` — returns JSON:

```json
{
  "week_label": "2026-W20",
  "summary": {
    "total_km": 152.3,
    "avg_speed_kmh": 23.1,
    "elevation_m": 858,
    "km_vs_prev_week_pct": 18,
    "speed_vs_prev_week_pct": 5,
    "elevation_vs_prev_week_pct": 12,
    "sparklines": {
      "km":        [48.0, null, 30.0, 48.5, null, 60.0, 90.0, 152.3],
      "speed_eas": [22.1, null, 23.8, 22.0, null, 23.0, 22.5, 23.1],
      "elevation": [210, null, 310, 195, null, 420, 380, 858]
    }
  },
  "activities": [
    {
      "id": 18529139427,
      "name": "Seen Runde",
      "sport_type": "GravelRide",
      "date": "16.05.",
      "distance_km": 75.3,
      "duration_min": 193,
      "avg_speed_kmh": 23.3,
      "grade": "A+",
      "ai_comment": "Deine längste Fahrt bisher…"
    }
  ],
  "trends": {
    "labels": ["2026-W17", "2026-W18", "2026-W19", "2026-W20"],
    "speed_eas": [22.1, null, 23.8, 24.2],
    "volume_km": [48.0, null, 30.0, 152.3]
  }
}
```

`summary.sparklines` always covers the last 8 ISO weeks (oldest first), with `null` for weeks with no activities. These are rendered as SVG sparklines inside the three summary cards. `activities` shows the 10 most recent activities filtered by `sport_type` (regardless of `weeks`). `trends` respects the `weeks` filter.

---

## File Changes

```
strava-mcp/
├── lib/
│   ├── db.py          ← add ai_comment migration in init_db()
│   ├── grade.py       ← NEW: composite grading
│   └── ai_coach.py    ← NEW: Haiku comment generation
├── sync.py            ← call generate_missing_comments() after sync
└── dashboard.py       ← full redesign (Tailwind + Stitch design system)
```

No changes to `main.py`, `lib/api.py`, `lib/helpers.py`.

---

## Dependencies

`anthropic` (Claude SDK) — add to `pyproject.toml`. Already have `python-dotenv`, `httpx`, `fastapi`, `uvicorn`.

The `.env` file already contains `ANTHROPIC_API_KEY` or it needs to be added (document in `.env.example`).

---

## Out of Scope

- Powermeter / cadence / resting HR (no data source)
- Mobile app
- Authentication
- Persistent server / systemd service
