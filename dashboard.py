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
