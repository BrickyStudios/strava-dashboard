"""Local performance dashboard — FastAPI + Tailwind at localhost:8080."""

import html
import json
import re
import webbrowser
from datetime import date, timedelta
from typing import Optional

from lib.surfaces import analyze_surfaces

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from lib.db import get_conn, init_db, get_activities, get_my_records, get_all_ranked_efforts
from lib.grade import compute_grade
from lib.ai_coach import generate_detail_comment, _get_api_key, _enrich_from_raw
import anthropic as _anthropic

app = FastAPI()


def _short_date(d: date) -> str:
    return f"{d:%b} {d.day}"  # "May 5"


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

    cur = buckets.get(cur_week, {"dist": 0, "time": 0, "elev": 0})
    # Compare like with like: last week only up to today's weekday, so Monday doesn't read as -100 %.
    prev_until = prev_monday + timedelta(days=today.weekday())
    prev = {"dist": 0.0, "time": 0, "elev": 0.0}
    for r in row_dicts:
        if prev_monday <= date.fromisoformat(r["start_date_local"][:10]) <= prev_until:
            prev["dist"] += r.get("distance_m") or 0
            prev["time"] += r.get("moving_time_s") or 0
            prev["elev"] += r.get("elevation_gain_m") or 0

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

    # --- Activities: 10 most recent ---
    recent = sorted(row_dicts, key=lambda r: r.get("start_date_local") or "", reverse=True)[:10]
    activity_list = []
    for r in recent:
        sport = r.get("sport_type")
        sport_peers = [a for a in row_dicts if a.get("sport_type") == sport]
        grade = compute_grade(r, sport_peers)
        start = (r.get("start_date_local") or "")[:10]
        try:
            d = date.fromisoformat(start)
            date_str = _short_date(d)
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
            "speed_vs_prev_week_pct": _pct_change(cur_eas, prev_eas) if cur["time"] else None,
            "elevation_vs_prev_week_pct": _pct_change(cur_elev, prev_elev),
        },
        "activities": activity_list,
        "trends": {
            "labels": labels,
            "speed_eas": speed_eas_series,
            "volume_km": volume_km_series,
        },
    }


def _surface_data(r: dict, raw: dict) -> dict | None:
    surface_raw = r.get("surface_json")
    if surface_raw:
        data = json.loads(surface_raw)
        if data.get("no_route"):
            return None
        return data
    # Not yet analyzed — compute on-the-fly and cache
    polyline = (raw.get("map") or {}).get("summary_polyline")
    if not polyline:
        return None
    result = analyze_surfaces(polyline)
    if result and not result.get("no_route"):
        conn = get_conn()
        conn.execute(
            "UPDATE activities SET surface_json = ? WHERE id = ?",
            (json.dumps(result), r["id"]),
        )
        conn.commit()
        return result
    return None


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
        date_str = _short_date(d)
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
        "avg_watts": round(avg_watts) if avg_watts is not None else None,
        "kilojoules": round(r["kilojoules"]) if r.get("kilojoules") is not None else None,
        "pr_count": raw.get("pr_count") or 0,
        "grade": grade,
        "ai_comment": r.get("ai_comment"),
        "summary_polyline": (raw.get("map") or {}).get("summary_polyline"),
        "surface": _surface_data(r, raw),
    }


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

    # Return cached comment if available
    if r.get("detail_comment"):
        return {"comment": r["detail_comment"]}

    sport = r.get("sport_type")
    peers = [dict(a) for a in get_activities(conn, sport_type=sport)]
    grade = compute_grade(r, peers)
    activity_for_comment = _enrich_from_raw(r)

    api_key = _get_api_key()
    if not api_key:
        return {"comment": None, "error": "ANTHROPIC_API_KEY not configured"}

    client = _anthropic.Anthropic(api_key=api_key)
    comment = generate_detail_comment(activity_for_comment, grade, client, recent_same_sport=peers)
    if comment:
        conn.execute("UPDATE activities SET detail_comment = ? WHERE id = ?", (comment, activity_id))
        conn.commit()
    return {"comment": comment}


@app.get("/api/segments")
def api_segments():
    conn = get_conn()
    init_db(conn)

    records_raw = get_my_records(conn)
    record_ids = {r["segment_id"] for r in records_raw}
    all_efforts = get_all_ranked_efforts(conn)

    by_seg: dict[int, dict] = {}
    for e in all_efforts:
        sid = e["segment_id"]
        if sid not in by_seg:
            by_seg[sid] = {
                "name": e["segment_name"],
                "distance_m": e["segment_distance_m"],
                "times": [],
                "overall_ranks": [],
                "pr_ranks": [],
            }
        if e["elapsed_time_s"] is not None:
            by_seg[sid]["times"].append(e["elapsed_time_s"])
        if e["overall_rank"] is not None:
            by_seg[sid]["overall_ranks"].append(e["overall_rank"])
        if e["pr_rank"] is not None:
            by_seg[sid]["pr_ranks"].append(e["pr_rank"])

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
            return _short_date(d)
        except Exception:
            return iso[:10]

    records = [
        {
            "segment_id": r["segment_id"],
            "segment_name": r["segment_name"],
            "distance_m": r["segment_distance_m"],
            "elapsed_time_s": r["elapsed_time_s"],
            "activity_date": _fmt_date(r["activity_date"] or ""),
            "record_type": "KOM" if r["overall_rank"] == 1 else "PR",
        }
        for r in records_raw
    ]

    opportunities = []
    for sid, data in by_seg.items():
        if sid in record_ids:
            continue
        times = data["times"]
        min_overall = min(data["overall_ranks"]) if data["overall_ranks"] else None
        min_pr = min(data["pr_ranks"]) if data["pr_ranks"] else None
        trend_pct = _trend_pct(times)
        is_near = (min_overall in (2, 3)) or (min_pr in (2, 3))
        is_trending = trend_pct is not None and trend_pct < -3.0

        if is_near or is_trending:
            display_rank = min_overall if min_overall is not None else min_pr
            opportunities.append({
                "segment_id": sid,
                "segment_name": data["name"],
                "distance_m": data["distance_m"],
                "elapsed_time_s": min(times) if times else None,
                "overall_rank": display_rank,
                "trend_pct": trend_pct,
                "is_trending": is_trending,
            })

    opportunities.sort(key=lambda x: (x["overall_rank"] or 99, x["trend_pct"] or 0))
    return {"records": records, "opportunities": opportunities}


@app.get("/", response_class=HTMLResponse)
def root():
    conn = get_conn()
    init_db(conn)
    count = conn.execute("SELECT COUNT(*) FROM activities").fetchone()[0]
    if count == 0:
        return HTMLResponse(_empty_html())
    # sport filter offers what's actually in your data, most frequent first
    sports = [r[0] for r in conn.execute(
        "SELECT sport_type FROM activities WHERE sport_type IS NOT NULL "
        "GROUP BY sport_type ORDER BY COUNT(*) DESC").fetchall()]
    options = "".join(f'<option value="{html.escape(sp)}">{html.escape(_sport_label(sp))}</option>' for sp in sports)
    return HTMLResponse(_dashboard_html().replace("<!--SPORT_OPTIONS-->", options))


def _sport_label(sport_type: str) -> str:
    """'GravelRide' -> 'Gravel ride'."""
    words = re.findall(r"[A-Z][a-z]*|[a-z]+|\d+", sport_type) or [sport_type]
    return " ".join([words[0]] + [w.lower() for w in words[1:]])


def _empty_html() -> str:
    return """<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Strava Dashboard</title>
  <link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='7' fill='%23171815'/%3E%3Cpath d='M6 24l7-14 5 9 3-5 5 10z' fill='%23C6F432'/%3E%3C/svg%3E">

<script src="https://cdn.tailwindcss.com"></script></head>
<body class="bg-[#0C0D0B] text-[#F4F1EA] flex items-center justify-center min-h-screen" style="font-family: system-ui, sans-serif">
  <div class="text-center">
    <h2 class="text-2xl font-medium mb-4">No data yet</h2>
    <p class="text-[#A6A49D] mb-2">Sync your Strava activities:</p>
    <code class="bg-[#171815] px-3 py-1 rounded-md">uv run sync.py</code>
    <p class="text-[#A6A49D] mt-6 mb-2">Or try it with made-up demo data:</p>
    <code class="bg-[#171815] px-3 py-1 rounded-md">uv run dashboard.py --demo</code>
  </div>
</body></html>"""


def _dashboard_html() -> str:
    return r"""<!DOCTYPE html>
<html lang="en" class="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Strava Dashboard</title>
  <link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='7' fill='%23171815'/%3E%3Cpath d='M6 24l7-14 5 9 3-5 5 10z' fill='%23C6F432'/%3E%3C/svg%3E">

  <script src="https://cdn.tailwindcss.com?plugins=forms"></script>
  <link href="https://fonts.googleapis.com/css2?family=Archivo:wght@400;500;600&family=Big+Shoulders+Display:wght@600&display=swap" rel="stylesheet">
  <link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:wght,FILL@400,0..1&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
    tailwind.config = {
      darkMode: 'class',
      theme: {
        extend: {
          colors: {
            'bg':       '#0C0D0B',
            'surface':  '#171815',
            'surface-low': '#121310',
            'surface-high': '#22231F',
            'border':   '#2C2D29',
            'on-surface': '#F4F1EA',
            'muted':    '#A6A49D',
            'faint':    '#8A8881',
            'lime':     '#C6F432',
          },
          fontFamily: { sans: ['Archivo', 'system-ui', 'sans-serif'] },
          transitionTimingFunction: { DEFAULT: 'cubic-bezier(0.22, 1, 0.36, 1)' },
        }
      }
    }
  </script>
  <style>
    body { font-family: 'Archivo', system-ui, sans-serif; font-variant-numeric: tabular-nums; }
    /* race-bib figures: Big Shoulders is for numbers only, words stay in Archivo */
    .fig { font-family: 'Big Shoulders Display', Impact, sans-serif; font-weight: 600; line-height: 1; }
    /* grades step down the ink ramp; only A+ gets the volt */
    .grade-ap { background:#C6F432; color:#0C0D0B; }
    .grade-a  { background:#22231F; color:#F4F1EA; box-shadow: inset 0 0 0 1px #2C2D29; }
    .grade-bp { background:#22231F; color:#A6A49D; box-shadow: inset 0 0 0 1px #2C2D29; }
    .grade-b  { color:#A6A49D; box-shadow: inset 0 0 0 1px #2C2D29; }
    .grade-c  { color:#8A8881; box-shadow: inset 0 0 0 1px #2C2D29; }
    .ax { fill:#8A8881; font-size:11px; }
    :focus-visible { outline: 2px solid #C6F432; outline-offset: 2px; }
  </style>
</head>
<body class="bg-bg text-on-surface min-h-screen">

  <!-- Header -->
  <header class="sticky top-0 z-10 bg-bg border-b border-border">
    <div class="max-w-5xl mx-auto px-4 sm:px-6 py-3 flex flex-wrap items-center justify-between gap-x-4 gap-y-3">
      <h1 class="flex items-center gap-2 text-base font-semibold text-on-surface">
        <svg class="w-5 h-5 text-lime" viewBox="0 0 32 32" aria-hidden="true"><path d="M6 24l7-14 5 9 3-5 5 10z" fill="currentColor"/></svg>
        Strava Dashboard
      </h1>
      <div class="flex gap-2 w-full sm:w-auto">
        <select id="sport" aria-label="Sport" class="flex-1 sm:flex-none h-9 bg-surface border border-border text-on-surface text-sm rounded-lg pl-3 pr-8 focus:outline-none focus:ring-1 focus:ring-lime focus:border-lime">
          <option value="" selected>All sports</option>
          <!--SPORT_OPTIONS-->
        </select>
        <select id="weeks" aria-label="Time range" class="flex-1 sm:flex-none h-9 bg-surface border border-border text-on-surface text-sm rounded-lg pl-3 pr-8 focus:outline-none focus:ring-1 focus:ring-lime focus:border-lime">
          <option value="4">4 weeks</option>
          <option value="12" selected>12 weeks</option>
          <option value="26">26 weeks</option>
          <option value="52">52 weeks</option>
        </select>
      </div>
    </div>
  </header>

  <!-- Tab bar -->
  <nav class="border-b border-border">
    <div class="max-w-5xl mx-auto px-4 sm:px-6 flex gap-6">
      <button id="tab-dashboard" onclick="switchTab('dashboard')"
        class="py-3 text-sm font-medium border-b-2 border-lime text-on-surface transition-colors">
        Dashboard
      </button>
      <button id="tab-segmente" onclick="switchTab('segmente')"
        class="py-3 text-sm font-medium border-b-2 border-transparent text-muted hover:text-on-surface transition-colors">
        Segments
      </button>
    </div>
  </nav>

  <main class="max-w-5xl mx-auto px-4 sm:px-6 py-8">

    <div id="view-dashboard" class="space-y-10">

    <!-- Weekly Summary -->
    <section>
      <div class="flex justify-between items-baseline mb-3">
        <h2 class="text-lg font-medium text-on-surface">This week</h2>
        <span id="week-label" class="text-xs text-faint"></span>
      </div>
      <div class="grid grid-cols-3 gap-3">
        <div class="bg-surface-low border border-border rounded-xl p-4 sm:p-5">
          <p class="text-sm text-muted">Distance</p>
          <p class="mt-3 flex items-baseline gap-1">
            <span id="s-km" class="fig text-4xl sm:text-[2.75rem] text-on-surface">—</span>
            <span class="text-xs text-muted">km</span>
          </p>
          <p id="badge-km" class="mt-3 text-xs text-muted"></p>
        </div>
        <div class="bg-surface-low border border-border rounded-xl p-4 sm:p-5">
          <p class="text-sm text-muted">Speed EAS</p>
          <p class="mt-3 flex items-baseline gap-1">
            <span id="s-speed" class="fig text-4xl sm:text-[2.75rem] text-on-surface">—</span>
            <span class="text-xs text-muted">km/h</span>
          </p>
          <p id="badge-speed" class="mt-3 text-xs text-muted"></p>
        </div>
        <div class="bg-surface-low border border-border rounded-xl p-4 sm:p-5">
          <p class="text-sm text-muted">Elevation</p>
          <p class="mt-3 flex items-baseline gap-1">
            <span id="s-elev" class="fig text-4xl sm:text-[2.75rem] text-on-surface">—</span>
            <span class="text-xs text-muted">m</span>
          </p>
          <p id="badge-elev" class="mt-3 text-xs text-muted"></p>
        </div>
      </div>
    </section>

    <!-- Activity Feed -->
    <section>
      <h2 class="text-lg font-medium text-on-surface mb-3">Recent activities</h2>
      <div id="activity-feed" class="space-y-2"></div>
    </section>

    <!-- Trends: one main chart, distance per week, with speed underneath on the same weeks -->
    <section>
      <h2 class="text-lg font-medium text-on-surface mb-3">Trends</h2>
      <div class="bg-surface-low border border-border rounded-xl p-4 sm:p-5">
        <div class="flex justify-between items-baseline gap-3">
          <p class="text-sm text-muted">Distance per week</p>
          <p id="trend-km-badge" class="text-xs text-muted"></p>
        </div>
        <svg id="trend-km" class="block w-full h-56 mt-3" role="img" aria-label="Distance per week"></svg>
        <div class="flex justify-between items-baseline gap-3 mt-6">
          <p class="text-sm text-muted">Speed EAS</p>
          <p id="trend-speed-badge" class="text-xs text-muted"></p>
        </div>
        <svg id="trend-speed" class="block w-full h-14 mt-2" role="img" aria-label="Speed EAS per week"></svg>
      </div>
    </section>

    </div><!-- /view-dashboard -->

    <!-- Segments Tab -->
    <div id="view-segmente" class="hidden space-y-10">
      <section>
        <h2 class="text-lg font-medium text-on-surface mb-3">My records</h2>
        <div id="seg-koms"><p class="text-sm text-muted">Loading…</p></div>
      </section>
      <section>
        <h2 class="text-lg font-medium text-on-surface mb-1">Record chances</h2>
        <p class="text-sm text-muted mb-3">Segments where you rank 2nd–3rd or keep getting faster</p>
        <div id="seg-opps"><p class="text-sm text-muted">Loading…</p></div>
      </section>
    </div>

  </main>

  <script>
    const GRADE_CLASS = {'A+':'grade-ap','A':'grade-a','B+':'grade-bp','B':'grade-b','C':'grade-c'};
    const SPORT_ICON = {
      GravelRide: 'directions_bike', Ride: 'directions_bike', MountainBikeRide: 'directions_bike',
      EBikeRide: 'electric_bike', VirtualRide: 'directions_bike', Run: 'directions_run', TrailRun: 'directions_run',
      Walk: 'directions_walk', Hike: 'hiking', Swim: 'pool'
    };
    const VOLT = '#C6F432', BAR = '#62635C', GRID = '#2C2D29', LINE = '#A6A49D';

    // one decimal everywhere, so 13 km/h reads 13.0 like its neighbours
    const f1 = v => v == null ? '—' : Number(v).toFixed(1);
    const wk = label => label.split('-')[1];  // '2026-W47' -> 'W47'

    function pctBadge(pct, elId) {
      const el = document.getElementById(elId);
      if (!el || pct === null || pct === undefined) { if(el) el.textContent=''; return; }
      el.textContent = `${pct >= 0 ? '↑' : '↓'} ${Math.abs(pct)}% vs last week`;
    }

    function gradeEl(grade) {
      const cls = GRADE_CLASS[grade] || 'grade-c';
      return `<span class="w-9 h-9 rounded-md flex items-center justify-center fig text-lg shrink-0 ${cls}">${grade}</span>`;
    }

    function escapeHtml(s) {
      return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    }

    function activityCard(act) {
      const icon = SPORT_ICON[act.sport_type] || 'fitness_center';
      const comment = act.ai_comment
        ? `<span class="block text-[13px] leading-5 text-muted mt-1">${escapeHtml(act.ai_comment)}</span>` : '';
      return `
        <button type="button" class="w-full text-left bg-surface-low border border-border rounded-xl p-4 flex items-start gap-3 hover:bg-surface transition-colors" onclick="openDetail(${act.id})">
          <span class="w-10 h-10 rounded-full bg-surface-high flex items-center justify-center shrink-0">
            <span class="material-symbols-outlined text-muted text-[20px]">${icon}</span>
          </span>
          <span class="flex-1 min-w-0">
            <span class="block text-sm font-medium text-on-surface truncate">${escapeHtml(act.name)}</span>
            <span class="block text-xs text-muted mt-0.5">${act.date} · ${f1(act.distance_km)} km · ${act.duration_min} min · ${f1(act.avg_speed_kmh)} km/h EAS</span>
            ${comment}
          </span>
          ${gradeEl(act.grade)}
        </button>`;
    }

    function niceMax(v) {
      const p = 10 ** Math.floor(Math.log10(v));
      return [1, 2, 2.5, 5, 10].find(m => m * p >= v) * p;
    }

    // Both trend charts share the week columns; drawn at real pixel size so text stays crisp.
    let _trends = null;
    function drawTrends() {
      if (!_trends) return;
      const { labels, volume_km: km, speed_eas: eas } = _trends;
      const n = labels.length, last = n - 1, LEFT = 52;

      const bars = document.getElementById('trend-km');
      const W = bars.clientWidth, H = bars.clientHeight, BOTTOM = 22;
      bars.setAttribute('viewBox', `0 0 ${W} ${H}`);
      const slot = (W - LEFT) / n, bw = Math.max(2, Math.min(28, slot * 0.6));
      const max = niceMax(Math.max(1, ...km.map(v => v || 0)));
      const y = v => (H - BOTTOM) - v / max * (H - BOTTOM - 8);
      const every = Math.ceil(n / Math.max(1, Math.floor((W - LEFT) / 44)));
      let s = '';
      for (const g of [0, max / 2, max]) {
        s += `<line x1="${LEFT}" x2="${W}" y1="${y(g)}" y2="${y(g)}" stroke="${GRID}"/>`
           + `<text x="${LEFT - 8}" y="${y(g) + 4}" text-anchor="end" class="ax">${g} km</text>`;
      }
      km.forEach((v, i) => {
        const x = LEFT + slot * i + slot / 2;
        if (v) s += `<rect x="${x - bw / 2}" y="${y(v)}" width="${bw}" height="${(H - BOTTOM) - y(v)}" rx="2" fill="${i === last ? VOLT : BAR}"><title>${wk(labels[i])}: ${f1(v)} km</title></rect>`;
        if ((last - i) % every === 0) s += `<text x="${x}" y="${H - 4}" text-anchor="middle" class="ax">${wk(labels[i])}</text>`;
      });
      bars.innerHTML = s;

      const line = document.getElementById('trend-speed');
      const LW = line.clientWidth, LH = line.clientHeight;
      line.setAttribute('viewBox', `0 0 ${LW} ${LH}`);
      const valid = eas.filter(v => v !== null);
      if (!valid.length) { line.innerHTML = ''; return; }
      const lo = Math.min(...valid), hi = Math.max(...valid), range = hi - lo || 1;
      const ly = v => LH - 6 - (v - lo) / range * (LH - 12);
      const lslot = (LW - LEFT) / n;
      let d = '', pen = false, t = '';
      eas.forEach((v, i) => {
        if (v === null) { pen = false; return; }
        d += `${pen ? 'L' : 'M'}${(LEFT + lslot * i + lslot / 2).toFixed(1)},${ly(v).toFixed(1)}`;
        pen = true;
      });
      t += `<text x="${LEFT - 8}" y="${ly(hi) + 4}" text-anchor="end" class="ax">${f1(hi)}</text>`
         + `<text x="${LEFT - 8}" y="${ly(lo) + 4}" text-anchor="end" class="ax">${f1(lo)}</text>`;
      const cur = eas[last] !== null
        ? `<circle cx="${LEFT + lslot * last + lslot / 2}" cy="${ly(eas[last])}" r="3.5" fill="${VOLT}"/>` : '';
      line.innerHTML = `${t}<path d="${d}" fill="none" stroke="${LINE}" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/>${cur}`;
    }
    window.addEventListener('resize', drawTrends);

    // Trend badge: last full week vs the first week with data. The current week is still running, so it's left out.
    function trendBadge(series, labels, elId) {
      const el = document.getElementById(elId);
      const idx = series.slice(0, -1).map((v, i) => v !== null ? i : -1).filter(i => i >= 0);
      if (idx.length < 2) { el.textContent = ''; return; }
      const a = idx[0], b = idx[idx.length - 1];
      const pct = Math.round((series[b] - series[a]) / series[a] * 100);
      el.textContent = `${pct >= 0 ? '↑' : '↓'} ${Math.abs(pct)}% · ${wk(labels[b])} vs ${wk(labels[a])}`;
    }

    async function load() {
      const sport = document.getElementById('sport').value;
      const weeks = document.getElementById('weeks').value;
      const r = await fetch(`/api/data?sport_type=${sport}&weeks=${weeks}`);
      const d = await r.json();

      // Summary
      document.getElementById('week-label').textContent = d.week_label;
      document.getElementById('s-km').textContent = f1(d.summary.total_km);
      document.getElementById('s-speed').textContent = f1(d.summary.avg_speed_kmh);
      document.getElementById('s-elev').textContent = d.summary.elevation_m ?? '—';
      pctBadge(d.summary.km_vs_prev_week_pct, 'badge-km');
      pctBadge(d.summary.speed_vs_prev_week_pct, 'badge-speed');
      pctBadge(d.summary.elevation_vs_prev_week_pct, 'badge-elev');

      // Activities
      const feed = document.getElementById('activity-feed');
      feed.innerHTML = d.activities.length
        ? d.activities.map(activityCard).join('')
        : '<p class="text-muted text-sm">No activities found</p>';

      // Trends
      _trends = d.trends;
      drawTrends();
      trendBadge(d.trends.volume_km, d.trends.labels, 'trend-km-badge');
      trendBadge(d.trends.speed_eas, d.trends.labels, 'trend-speed-badge');
    }

    document.getElementById('sport').addEventListener('change', load);
    document.getElementById('weeks').addEventListener('change', load);
    load();

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
        container.innerHTML = '<span class="text-xs text-muted">No route available</span>';
        return;
      }
      container.innerHTML = '';
      if (_leafletMap) { _leafletMap.remove(); _leafletMap = null; }
      const coords = decodePolyline(polyline);
      _leafletMap = L.map(container, { zoomControl: false });
      L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      }).addTo(_leafletMap);
      // dark casing under the volt line, so it reads on the light green tiles
      L.polyline(coords, { color: '#0C0D0B', weight: 6, opacity: 0.55 }).addTo(_leafletMap);
      const line = L.polyline(coords, { color: VOLT, weight: 3 }).addTo(_leafletMap);
      _leafletMap.fitBounds(line.getBounds(), { padding: [8, 8] });
    }

    function statCard(label, value, unit='') {
      if (value === null || value === undefined) return '';
      return `<div class="bg-surface-low border border-border rounded-xl p-3">
        <p class="text-xs text-muted">${label}</p>
        <p class="mt-2 flex items-baseline gap-1"><span class="fig text-2xl text-on-surface">${value}</span><span class="text-xs text-muted">${unit}</span></p>
      </div>`;
    }

    const SURFACE_COLORS = {
      asphalt: { bg: '#4a4a54', label: 'Asphalt' },
      gravel:  { bg: '#b86b30', label: 'Gravel' },
      trail:   { bg: '#5a7a3a', label: 'Trail' },
      unknown: { bg: '#2C2D29', label: 'Unknown' },
    };

    function renderSurface(surface) {
      const section = document.getElementById('panel-surface-section');
      const container = document.getElementById('panel-surface');
      if (!surface) { section.style.display = 'none'; return; }

      const total = surface.total_analyzed_m || 1;
      const slices = [
        { key: 'asphalt', m: surface.asphalt_m || 0 },
        { key: 'gravel',  m: surface.gravel_m  || 0 },
        { key: 'trail',   m: surface.trail_m   || 0 },
        { key: 'unknown', m: surface.unknown_m || 0 },
      ].filter(s => s.m > 0);

      // Stacked bar
      const barSegs = slices.map(s => {
        const pct = s.m / total * 100;
        const c = SURFACE_COLORS[s.key];
        return `<div style="width:${pct.toFixed(1)}%;background:${c.bg};height:100%;display:inline-block;" title="${c.label}: ${(s.m/1000).toFixed(1)} km"></div>`;
      }).join('');

      // Legend rows
      const legend = slices.map(s => {
        const c = SURFACE_COLORS[s.key];
        const pct = Math.round(s.m / total * 100);
        const km = (s.m / 1000).toFixed(1);
        return `<div class="flex items-center gap-2 text-xs">
          <div class="w-3 h-3 rounded-sm shrink-0" style="background:${c.bg}"></div>
          <span class="text-on-surface w-20">${c.label}</span>
          <span class="text-muted">${pct}%</span>
          <span class="text-muted ml-auto">${km} km</span>
        </div>`;
      }).join('');

      const analyzed_km = (total / 1000).toFixed(1);
      container.innerHTML = `
        <div class="w-full h-3 rounded-sm overflow-hidden flex mb-3 bg-surface-high">${barSegs}</div>
        <div class="space-y-2">${legend}</div>
        <p class="text-xs text-faint mt-3">${analyzed_km} km analyzed</p>`;
      section.style.display = 'block';
    }

    async function loadDetailComment(id) {
      const el = document.getElementById('panel-ai');
      try {
        const r = await fetch(`/api/activity/${id}/detail-comment`);
        const d = await r.json();
        el.textContent = d.comment || 'No analysis available';
      } catch {
        el.textContent = 'Could not load the analysis';
      }
    }

    async function openDetail(id) {
      const panel = document.getElementById('detail-panel');
      const overlay = document.getElementById('panel-overlay');

      // Reset panel state
      document.getElementById('panel-name').textContent = '…';
      document.getElementById('panel-meta').textContent = '';
      document.getElementById('panel-grade').textContent = '';
      document.getElementById('panel-grade').className = 'w-9 h-9 rounded-md flex items-center justify-center fig text-lg shrink-0';
      document.getElementById('panel-stats').innerHTML = '';
      document.getElementById('panel-map').innerHTML = '<span class="text-xs text-muted">Loading…</span>';
      document.getElementById('panel-surface-section').style.display = 'none';
      document.getElementById('panel-ai').innerHTML = '<span class="text-muted">Loading analysis…</span>';

      // Slide in
      overlay.classList.remove('hidden');
      requestAnimationFrame(() => panel.classList.remove('translate-x-full'));

      try {
        const r = await fetch(`/api/activity/${id}`);
        if (!r.ok) return;
        const d = await r.json();

        document.getElementById('panel-name').textContent = d.name;
        document.getElementById('panel-meta').textContent =
          `${d.date} · ${sportLabel(d.sport_type)} · Grade ${d.grade}`;

        const gradeEl = document.getElementById('panel-grade');
        gradeEl.textContent = d.grade;
        gradeEl.className = `w-9 h-9 rounded-md flex items-center justify-center fig text-lg shrink-0 ${GRADE_CLASS[d.grade] || 'grade-c'}`;

        document.getElementById('panel-stats').innerHTML = [
          statCard('Distance', f1(d.distance_km), 'km'),
          statCard('Duration', d.duration_min, 'min'),
          statCard('Avg speed', f1(d.avg_speed_kmh), 'km/h'),
          statCard('Speed EAS', f1(d.eas_kmh), 'km/h'),
          statCard('Max speed', f1(d.max_speed_kmh), 'km/h'),
          statCard('Elevation', d.elevation_gain_m, 'm'),
          statCard('Highest point', d.elev_high_m != null ? Math.round(d.elev_high_m) : null, 'm'),
          statCard('Lowest point', d.elev_low_m != null ? Math.round(d.elev_low_m) : null, 'm'),
          statCard('Avg heart rate', d.avg_heartrate != null ? Math.round(d.avg_heartrate) : null, 'bpm'),
          statCard('Max heart rate', d.max_heartrate, 'bpm'),
          statCard('Avg power', d.avg_watts, 'W'),
          statCard('Energy', d.kilojoules, 'kJ'),
          statCard('Strava PRs', d.pr_count || null, ''),
        ].join('');

        initMap(d.summary_polyline);
        renderSurface(d.surface);
        loadDetailComment(id);
      } catch (e) {
        console.error(e);
      }
    }

    // 'GravelRide' -> 'Gravel ride', same as the sport filter
    function sportLabel(t) {
      const w = (t || '').match(/[A-Z][a-z]*|[a-z]+|\d+/g) || [t];
      return [w[0], ...w.slice(1).map(x => x.toLowerCase())].join(' ');
    }
    document.addEventListener('keydown', e => { if (e.key === 'Escape') closePanel(); });

    function closePanel() {
      const panel = document.getElementById('detail-panel');
      const overlay = document.getElementById('panel-overlay');
      panel.classList.add('translate-x-full');
      overlay.classList.add('hidden');
      if (_leafletMap) { _leafletMap.remove(); _leafletMap = null; }
    }

    function fmtTime(s) {
      if (s == null) return '—';
      const m = Math.floor(s / 60), sec = s % 60;
      return `${m}:${String(sec).padStart(2, '0')}`;
    }

    function fmtDist(m) {
      return m != null ? (m / 1000).toFixed(1) + ' km' : '—';
    }

    // num: right-aligned figure column; wide: hidden on phones so the segment name keeps room
    function segTable(rows, cols) {
      if (!rows.length) return '<p class="text-sm text-muted">No data</p>';
      const cls = c => `${c.num ? 'text-right whitespace-nowrap' : 'text-left'} ${c.wide ? 'hidden sm:table-cell' : ''}`;
      const head = cols.map(c =>
        `<th class="${cls(c)} text-xs font-medium text-muted px-3 sm:px-5 py-2.5">${c.label}</th>`
      ).join('');
      const body = rows.map(r =>
        `<tr class="border-t border-border hover:bg-surface transition-colors">
          ${cols.map(c => `<td class="${cls(c)} px-3 sm:px-5 py-2.5 text-sm text-on-surface">${c.render(r)}</td>`).join('')}
        </tr>`
      ).join('');
      return `<div class="bg-surface-low border border-border rounded-xl overflow-x-auto"><table class="w-full">
        <thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
    }

    let _segLoaded = false;

    async function loadSegments() {
      if (_segLoaded) return;
      _segLoaded = true;
      try {
        const r = await fetch('/api/segments');
        const d = await r.json();

        if (!d.records.length && !d.opportunities.length) {
          document.getElementById('seg-koms').innerHTML =
            '<p class="text-sm text-muted">No segment data yet. Run <code class="bg-surface px-1 rounded">uv run sync.py</code> to load it.</p>';
          document.getElementById('seg-opps').innerHTML = '';
          return;
        }

        document.getElementById('seg-koms').innerHTML = segTable(d.records, [
          { label: 'Segment',   render: r => escapeHtml(r.segment_name) },
          { label: 'Distance',  num: true, wide: true, render: r => fmtDist(r.distance_m) },
          { label: 'Type',      render: r => `<span class="${r.record_type === 'KOM' ? 'text-lime font-medium' : 'text-muted'}">${r.record_type}</span>` },
          { label: 'Best time', num: true, render: r => fmtTime(r.elapsed_time_s) },
          { label: 'Date',      num: true, render: r => escapeHtml(r.activity_date || '—') },
        ]);

        document.getElementById('seg-opps').innerHTML = segTable(d.opportunities, [
          { label: 'Segment',  render: r => escapeHtml(r.segment_name) },
          { label: 'Distance', num: true, wide: true, render: r => fmtDist(r.distance_m) },
          { label: 'My time',  num: true, render: r => fmtTime(r.elapsed_time_s) },
          { label: 'Rank',     num: true, render: r => r.overall_rank ? `#${r.overall_rank}` : '—' },
          { label: 'Trend',    num: true, render: r => r.is_trending
              ? `${Math.abs(r.trend_pct)}% faster`
              : '<span class="text-faint">—</span>' },
        ]);
      } catch (e) {
        console.error('Segment load error:', e);
      }
    }

    function switchTab(tab) {
      const isDash = tab === 'dashboard';
      document.getElementById('view-dashboard').classList.toggle('hidden', !isDash);
      document.getElementById('view-segmente').classList.toggle('hidden', isDash);
      const active = 'border-lime text-on-surface';
      const inactive = 'border-transparent text-muted hover:text-on-surface';
      document.getElementById('tab-dashboard').className =
        `py-3 text-sm font-medium border-b-2 ${isDash ? active : inactive} transition-colors`;
      document.getElementById('tab-segmente').className =
        `py-3 text-sm font-medium border-b-2 ${isDash ? inactive : active} transition-colors`;
      if (isDash) drawTrends(); else loadSegments();
    }
  </script>

  <!-- Overlay -->
  <div id="panel-overlay" class="fixed inset-0 bg-black/50 z-20 hidden" onclick="closePanel()"></div>

  <!-- Detail Panel -->
  <aside id="detail-panel"
    class="fixed top-0 right-0 h-full w-full sm:w-[420px] bg-surface border-l border-border z-30
           transform translate-x-full transition-transform duration-300 motion-reduce:transition-none overflow-y-auto">

    <!-- Panel Header -->
    <div class="sticky top-0 z-[1000] bg-surface border-b border-border px-4 py-3 flex items-center gap-2">
      <button onclick="closePanel()" aria-label="Close" class="w-10 h-10 -ml-2 rounded-lg flex items-center justify-center text-muted hover:text-on-surface hover:bg-surface-high transition-colors">
        <span class="material-symbols-outlined text-[20px]">arrow_back</span>
      </button>
      <div class="flex-1 min-w-0">
        <p id="panel-name" class="text-sm font-medium text-on-surface truncate"></p>
        <p id="panel-meta" class="text-xs text-muted"></p>
      </div>
      <div id="panel-grade" class="w-9 h-9 rounded-md flex items-center justify-center fig text-lg shrink-0"></div>
    </div>

    <!-- Stats Grid -->
    <div class="px-4 pt-4 pb-6">
      <h3 class="text-sm font-medium text-muted mb-3">Stats</h3>
      <div id="panel-stats" class="grid grid-cols-2 gap-2"></div>
    </div>

    <!-- Route Map -->
    <div class="px-4 pb-6">
      <h3 class="text-sm font-medium text-muted mb-3">Route</h3>
      <div id="panel-map" class="rounded-xl overflow-hidden h-48 bg-surface-low border border-border
                                  flex items-center justify-center text-muted text-xs">
        <span>No route available</span>
      </div>
    </div>

    <!-- Surface Analysis -->
    <div class="px-4 pb-6" id="panel-surface-section" style="display:none">
      <h3 class="text-sm font-medium text-muted mb-3">Surface</h3>
      <div id="panel-surface"></div>
    </div>

    <!-- AI Analysis -->
    <div class="px-4 pb-8">
      <h3 class="text-sm font-medium text-muted mb-3">AI analysis</h3>
      <div id="panel-ai" class="text-sm text-on-surface leading-relaxed">
        <span class="text-muted">Loading analysis…</span>
      </div>
    </div>
  </aside>
</body>
</html>"""


def main() -> None:
    import argparse
    import os
    parser = argparse.ArgumentParser(description="Local Strava dashboard")
    parser.add_argument("--demo", action="store_true", help="Run with made-up demo data, no Strava account needed")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if args.demo:
        from lib.demo import DEMO_DB, build_demo_db
        build_demo_db(DEMO_DB)  # rebuilt each start so the dates stay recent
        os.environ["STRAVA_DASHBOARD_DB"] = str(DEMO_DB)
        print(f"Demo mode: {DEMO_DB}")
    url = f"http://localhost:{args.port}"
    if not args.no_browser:
        webbrowser.open(url)
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
