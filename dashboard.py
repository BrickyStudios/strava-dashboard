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
  <link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='7' fill='%23131316'/%3E%3Cpath d='M6 24l7-14 5 9 3-5 5 10z' fill='%23abd600'/%3E%3C/svg%3E">

<script src="https://cdn.tailwindcss.com"></script></head>
<body class="bg-[#0C0D0B] text-[#F4F1EA] flex items-center justify-center min-h-screen">
  <div class="text-center">
    <h2 class="text-2xl font-semibold mb-4">No data yet</h2>
    <p class="text-[#A6A49D] mb-2">Sync your Strava activities:</p>
    <code class="bg-[#171815] px-3 py-1 rounded text-[#C6F432]">uv run sync.py</code>
    <p class="text-[#A6A49D] mt-6 mb-2">Or try it with made-up demo data:</p>
    <code class="bg-[#171815] px-3 py-1 rounded text-[#C6F432]">uv run dashboard.py --demo</code>
  </div>
</body></html>"""


def _dashboard_html() -> str:
    return """<!DOCTYPE html>
<html lang="en" class="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Strava Dashboard</title>
  <link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='7' fill='%23131316'/%3E%3Cpath d='M6 24l7-14 5 9 3-5 5 10z' fill='%23abd600'/%3E%3C/svg%3E">

  <script src="https://cdn.tailwindcss.com?plugins=forms"></script>
  <link href="https://fonts.googleapis.com/css2?family=Archivo:wght@400;500;600;700;800&family=Big+Shoulders+Display:wght@700;800;900&display=swap" rel="stylesheet">
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
            'lime':     '#C6F432',
            'lime-bright': '#D8FF5C',
            'orange':   '#FF5B1F',
          },
          fontFamily: { sans: ['Archivo', 'system-ui', 'sans-serif'], display: ['"Big Shoulders Display"', 'Impact', 'sans-serif'] },
        }
      }
    }
  </script>
  <style>
    /* FitShare look: race-bib numbers in Big Shoulders, text in Archivo, volt on ink */
    body { font-family: 'Archivo', system-ui, sans-serif; }
    h2, .text-3xl, .grade-ap, .grade-a, .grade-bp, .grade-b, .grade-c { font-family: 'Big Shoulders Display', Impact, sans-serif; }
    h2 { text-transform: uppercase; font-weight: 800; letter-spacing: .02em; font-size: 1.6rem; }
    section + section h2, div + h2 { margin-top: 2rem; }
    .text-3xl { font-size: 2.9rem; line-height: 1; font-weight: 800; letter-spacing: -.01em; }
    .grade-ap, .grade-a, .grade-bp, .grade-b, .grade-c { font-size: 1rem; font-weight: 800; }
    .grade-ap { background:#D8FF5C; color:#0C0D0B; }
    .grade-a  { background:#9DC22A; color:#0C0D0B; }
    .grade-bp { background:#FF5B1F; color:#0C0D0B; }
    .grade-b  { background:#22231F; color:#F4F1EA; border:1px solid #2C2D29; }
    .grade-c  { background:#121310; color:#7C7A74; border:1px solid #2C2D29; }
  </style>
</head>
<body class="bg-bg text-on-surface min-h-screen">

  <!-- Header -->
  <header class="sticky top-0 z-10 bg-bg/80 backdrop-blur border-b border-border px-6 py-3 flex items-center justify-between">
    <h1 class="text-sm font-extrabold tracking-[.3em] text-on-surface flex items-center gap-2"><span class="w-2.5 h-2.5 rounded-full bg-lime inline-block"></span>STRAVA DASHBOARD</h1>
    <div class="flex gap-3 items-center">
      <select id="sport" class="bg-surface border border-border text-on-surface text-sm rounded px-2 py-1 focus:outline-none focus:border-lime">
        <option value="" selected>All sports</option>
        <!--SPORT_OPTIONS-->
      </select>
      <select id="weeks" class="bg-surface border border-border text-on-surface text-sm rounded px-2 py-1 focus:outline-none focus:border-lime">
        <option value="4">4 weeks</option>
        <option value="12" selected>12 weeks</option>
        <option value="26">26 weeks</option>
        <option value="52">52 weeks</option>
      </select>
    </div>
  </header>

  <!-- Tab bar -->
  <nav class="border-b border-border px-4">
    <div class="max-w-3xl mx-auto flex">
      <button id="tab-dashboard" onclick="switchTab('dashboard')"
        class="px-4 py-3 text-sm font-semibold border-b-2 border-lime text-lime transition-colors">
        Dashboard
      </button>
      <button id="tab-segmente" onclick="switchTab('segmente')"
        class="px-4 py-3 text-sm font-semibold border-b-2 border-transparent text-muted hover:text-on-surface transition-colors">
        Segments
      </button>
    </div>
  </nav>

  <main class="max-w-3xl mx-auto px-4 py-6 space-y-8">

    <div id="view-dashboard">

    <!-- Weekly Summary -->
    <section>
      <div class="flex justify-between items-baseline mb-3">
        <h2 class="text-base font-semibold text-on-surface">This week</h2>
        <span id="week-label" class="text-xs text-muted"></span>
      </div>
      <div class="grid grid-cols-3 gap-3">
        <div class="bg-surface-low border border-border rounded-lg p-4 flex flex-col">
          <span class="text-xs font-bold uppercase tracking-wider text-muted mb-3">Distance</span>
          <div class="flex items-baseline gap-1 mb-3">
            <span id="s-km" class="text-3xl font-bold text-on-surface">—</span>
            <span class="text-xs text-muted">km</span>
          </div>
          <div id="badge-km" class="text-xs mb-2"></div>
          <svg id="spark-km" class="mt-auto w-full h-8" viewBox="0 0 80 24" preserveAspectRatio="none"></svg>
        </div>
        <div class="bg-surface-low border border-border rounded-lg p-4 flex flex-col">
          <span class="text-xs font-bold uppercase tracking-wider text-muted mb-3">Speed EAS</span>
          <div class="flex items-baseline gap-1 mb-3">
            <span id="s-speed" class="text-3xl font-bold text-on-surface">—</span>
            <span class="text-xs text-muted">km/h</span>
          </div>
          <div id="badge-speed" class="text-xs mb-2"></div>
          <svg id="spark-speed" class="mt-auto w-full h-8" viewBox="0 0 80 24" preserveAspectRatio="none"></svg>
        </div>
        <div class="bg-surface-low border border-border rounded-lg p-4 flex flex-col">
          <span class="text-xs font-bold uppercase tracking-wider text-muted mb-3">Elevation</span>
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
      <h2 class="text-base font-semibold text-on-surface mb-3">Recent activities</h2>
      <div id="activity-feed" class="space-y-2"></div>
    </section>

    <!-- Trends -->
    <section>
      <h2 class="text-base font-semibold text-on-surface mb-3">Trends</h2>
      <div class="grid grid-cols-2 gap-3">
        <div class="bg-surface-low border border-border rounded-lg p-4">
          <div class="flex justify-between items-center mb-2">
            <span class="text-xs font-bold uppercase tracking-wider text-muted">Speed EAS</span>
            <span id="trend-speed-badge" class="text-xs font-semibold text-lime"></span>
          </div>
          <svg id="trend-speed" class="w-full h-16" viewBox="0 0 200 48" preserveAspectRatio="none"></svg>
        </div>
        <div class="bg-surface-low border border-border rounded-lg p-4">
          <div class="flex justify-between items-center mb-2">
            <span class="text-xs font-bold uppercase tracking-wider text-muted">km / week</span>
            <span id="trend-km-badge" class="text-xs font-semibold text-orange"></span>
          </div>
          <svg id="trend-km" class="w-full h-16" viewBox="0 0 200 48" preserveAspectRatio="none"></svg>
        </div>
      </div>
    </section>

    </div><!-- /view-dashboard -->

    <!-- Segments Tab -->
    <div id="view-segmente" class="hidden space-y-6">
      <section>
        <h2 class="text-base font-semibold text-on-surface mb-3">My records</h2>
        <div id="seg-koms"><p class="text-sm text-muted">Loading…</p></div>
      </section>
      <section>
        <h2 class="text-base font-semibold text-on-surface mb-3">Record chances</h2>
        <p class="text-xs text-muted mb-3">Segments where you rank 2nd–3rd or keep getting faster.</p>
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
      el.innerHTML = `<span class="${cls}">${arrow} ${Math.abs(pct)}% vs last week</span>`;
    }

    function gradeEl(grade) {
      const cls = GRADE_CLASS[grade] || 'grade-c';
      return `<div class="w-9 h-9 rounded-lg flex items-center justify-center text-xs font-bold shrink-0 ${cls}">${grade}</div>`;
    }

    function escapeHtml(s) {
      return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    }

    function activityCard(act) {
      const icon = SPORT_ICON[act.sport_type] || 'fitness_center';
      const comment = act.ai_comment
        ? `<p class="text-xs text-muted italic mt-1">${escapeHtml(act.ai_comment)}</p>` : '';
      return `
        <div class="bg-surface-low border border-border rounded-lg p-3 flex items-start gap-3 hover:border-lime/30 transition-colors cursor-pointer" onclick="openDetail(${act.id})">
          <div class="w-10 h-10 rounded-full bg-surface-high flex items-center justify-center shrink-0">
            <span class="material-symbols-outlined text-muted text-lg">${icon}</span>
          </div>
          <div class="flex-1 min-w-0">
            <p class="text-sm font-semibold text-on-surface truncate">${escapeHtml(act.name)}</p>
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
      renderSparkline('spark-km', d.summary.sparklines.km, '#C6F432');
      renderSparkline('spark-speed', d.summary.sparklines.speed_eas, '#C6F432');
      renderSparkline('spark-elev', d.summary.sparklines.elevation, '#C6F432');

      // Activities
      const feed = document.getElementById('activity-feed');
      feed.innerHTML = d.activities.length
        ? d.activities.map(activityCard).join('')
        : '<p class="text-muted text-sm">No activities found.</p>';

      // Trends
      renderSparkline('trend-speed', d.trends.speed_eas, '#C6F432');
      renderSparkline('trend-km', d.trends.volume_km, '#FF5B1F');

      // Trend badges: last vs first week. The current week is still running, so it's left out.
      function trendBadge(series, elId, color) {
        const vals = series.slice(0, -1).filter(v => v !== null);
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
      L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png').addTo(_leafletMap);
      const line = L.polyline(coords, { color: '#C6F432', weight: 3 }).addTo(_leafletMap);
      _leafletMap.fitBounds(line.getBounds(), { padding: [8, 8] });
    }

    function statCard(label, value, unit='') {
      if (value === null || value === undefined) return '';
      return `<div class="bg-surface-low border border-border rounded-lg p-3">
        <p class="text-xs text-muted mb-1">${label}</p>
        <p class="font-display text-2xl font-extrabold text-on-surface leading-none mt-1">${value}<span class="font-sans text-xs font-medium text-muted ml-1">${unit}</span></p>
      </div>`;
    }

    const SURFACE_COLORS = {
      asphalt: { bg: '#4a4a54', label: 'Asphalt',  text: '#A6A49D' },
      gravel:  { bg: '#b86b30', label: 'Gravel', text: '#fff' },
      trail:   { bg: '#5a7a3a', label: 'Trail',    text: '#fff' },
      unknown: { bg: '#2C2D29', label: 'Unknown',text: '#7C7A74' },
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
          <span class="text-on-surface font-medium w-20">${c.label}</span>
          <span class="text-muted">${pct}%</span>
          <span class="text-muted ml-auto">${km} km</span>
        </div>`;
      }).join('');

      const analyzed_km = (total / 1000).toFixed(1);
      container.innerHTML = `
        <div class="w-full h-4 rounded overflow-hidden flex mb-3 bg-surface-high">${barSegs}</div>
        <div class="space-y-1.5">${legend}</div>
        <p class="text-xs text-muted mt-2">${analyzed_km} km analysiert</p>`;
      section.style.display = 'block';
    }

    async function loadDetailComment(id) {
      const el = document.getElementById('panel-ai');
      try {
        const r = await fetch(`/api/activity/${id}/detail-comment`);
        const d = await r.json();
        el.textContent = d.comment || 'No analysis available.';
      } catch {
        el.textContent = 'Could not load the analysis.';
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
      document.getElementById('panel-map').innerHTML = '<span class="text-xs text-muted">Loading…</span>';
      document.getElementById('panel-surface-section').style.display = 'none';
      document.getElementById('panel-ai').innerHTML = '<span class="animate-pulse">Loading analysis…</span>';

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
        gradeEl.className = `w-9 h-9 rounded-lg flex items-center justify-center text-xs font-bold shrink-0 ${GRADE_CLASS[d.grade] || 'grade-c'}`;

        document.getElementById('panel-stats').innerHTML = [
          statCard('Distance', d.distance_km, 'km'),
          statCard('Duration', d.duration_min, 'min'),
          statCard('Avg speed', d.avg_speed_kmh, 'km/h'),
          statCard('Speed EAS', d.eas_kmh, 'km/h'),
          statCard('Max speed', d.max_speed_kmh, 'km/h'),
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

    function segTable(rows, cols) {
      if (!rows.length) return '<p class="text-sm text-muted">No data.</p>';
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

        if (!d.records.length && !d.opportunities.length) {
          document.getElementById('seg-koms').innerHTML =
            '<p class="text-sm text-muted">No segment data yet. Run <code class="bg-surface px-1 rounded">uv run sync.py</code> to load it.</p>';
          document.getElementById('seg-opps').innerHTML = '';
          return;
        }

        document.getElementById('seg-koms').innerHTML = segTable(d.records, [
          { label: 'Segment',    render: r => escapeHtml(r.segment_name) },
          { label: 'Distance',    render: r => fmtDist(r.distance_m) },
          { label: 'Type',        render: r => `<span class="font-semibold ${r.record_type === 'KOM' ? 'text-lime' : 'text-amber-400'}">${r.record_type}</span>` },
          { label: 'Best time', render: r => fmtTime(r.elapsed_time_s) },
          { label: 'Date',       render: r => escapeHtml(r.activity_date || '—') },
        ]);

        document.getElementById('seg-opps').innerHTML = segTable(d.opportunities, [
          { label: 'Segment',    render: r => escapeHtml(r.segment_name) },
          { label: 'Distance',    render: r => fmtDist(r.distance_m) },
          { label: 'My time', render: r => fmtTime(r.elapsed_time_s) },
          { label: 'Rank',       render: r => r.overall_rank ? `#${r.overall_rank}` : '—' },
          { label: 'Trend',      render: r => r.is_trending
              ? `<span class="text-lime font-semibold">${Math.abs(r.trend_pct)}% faster</span>`
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
  </script>

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
        <span>No route available</span>
      </div>
    </div>

    <!-- Surface Analysis -->
    <div class="px-4 pb-4" id="panel-surface-section" style="display:none">
      <h3 class="text-xs font-bold uppercase tracking-wider text-muted mb-3">Surface</h3>
      <div id="panel-surface"></div>
    </div>

    <!-- AI Analysis -->
    <div class="px-4 pb-8">
      <h3 class="text-xs font-bold uppercase tracking-wider text-muted mb-3">AI analysis</h3>
      <div id="panel-ai" class="text-sm text-muted italic leading-relaxed">
        <span class="animate-pulse">Loading analysis…</span>
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
