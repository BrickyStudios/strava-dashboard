# Segment Records & Opportunities — Design Spec

**Date:** 2026-05-20  
**Project:** strava-mcp dashboard  
**Status:** Approved

## Goal

Add a "Segmente" tab to the local Strava dashboard that shows:
1. Segments where the user holds the current record (KOM/CR, `overall_rank = 1`)
2. Segments where the user is close to a record or trending faster (rank 2–3 or ≥3% improvement)

## Data Layer

### New table: `segment_efforts`

```sql
CREATE TABLE IF NOT EXISTS segment_efforts (
    segment_id         INTEGER NOT NULL,
    segment_name       TEXT,
    segment_distance_m REAL,
    activity_id        INTEGER NOT NULL,
    elapsed_time_s     INTEGER,
    start_date_local   TEXT,
    pr_rank            INTEGER,  -- 1 = personal record
    overall_rank       INTEGER,  -- 1 = KOM, 2 = 2nd, NULL = unranked
    PRIMARY KEY (segment_id, activity_id)
);
```

Added via `ALTER TABLE`-style migration in `init_db()` (same pattern as existing `ai_comment`/`detail_comment` columns).

### Sync enhancement (`sync.py`)

After the main activity sync loop, a second pass fetches detail data for activities with no segment entries yet:

1. Query: `SELECT id FROM activities WHERE id NOT IN (SELECT DISTINCT activity_id FROM segment_efforts)`
2. For each such `activity_id`: call `GET /activities/{id}?include_all_efforts=true`
3. Parse `response["segment_efforts"]` array; for each effort:
   - Extract `segment.id`, `segment.name`, `segment.distance` (in meters)
   - Extract `elapsed_time`, `start_date_local`, `pr_rank`
   - Extract `overall_rank` from `achievements` array: find entry with `type == "overall"`, use its `rank`; default `NULL` if no such achievement
4. Upsert into `segment_efforts`
5. Rate-limit check reuses existing `check_rate_limit(resp.headers)` helper

Activities with zero segment efforts (e.g. trainer rides) are marked as processed by inserting a sentinel row (segment_id=0, segment_name=NULL) so they are not re-fetched every sync.

## API Layer

### `GET /api/segments`

Returns two lists derived purely from the local DB:

```json
{
  "koms": [
    {
      "segment_id": 12345,
      "segment_name": "Bergkuppe Sprint",
      "distance_m": 420,
      "elapsed_time_s": 54,
      "activity_date": "19.05.",
      "overall_rank": 1
    }
  ],
  "opportunities": [
    {
      "segment_id": 67890,
      "segment_name": "Waldweg Abfahrt",
      "distance_m": 1200,
      "elapsed_time_s": 198,
      "overall_rank": 2,
      "trend_pct": -8.4,
      "is_trending": true
    }
  ]
}
```

**KOMs query:** `SELECT ... FROM segment_efforts WHERE overall_rank = 1 GROUP BY segment_id` (best time per segment).

**Opportunities query:** Segments where `overall_rank IN (2, 3)` OR `is_trending = true`. Trend is computed per segment: if there are ≥2 efforts, compare the average of the chronologically first half vs the last half. If improvement exceeds 3%, mark as trending and include `trend_pct`.

Segments with `overall_rank = 1` are excluded from opportunities (already a KOM).

## UI Layer

New "Segmente" tab rendered in the existing single-page dashboard HTML. Tab switching uses the same vanilla JS pattern already present. The tab content is loaded on first click via `fetch('/api/segments')`.

### "Meine Rekorde" section

Table with columns: Segment · Distanz · Beste Zeit · Datum

### "Rekord-Chancen" section

Table with columns: Segment · Distanz · Meine Zeit · Platz · Trend

Trend column shows:
- `↑ -8.4%` if improving (green)
- `—` if no trend data

If no segment data exists yet (empty response), display:
> *Keine Segment-Daten vorhanden. Starte `python sync.py` um Daten zu laden.*

Styling follows existing Tailwind dark-mode card pattern.

## Constraints

- No new external dependencies
- Rate limits handled by existing `check_rate_limit()` in `sync.py`
- Sentinel row pattern avoids re-fetching segment-less activities on every sync
- Trend computed locally — no extra Strava API calls at dashboard load time
