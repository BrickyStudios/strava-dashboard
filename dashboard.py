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
        sport = r.get("sport_type")
        sport_peers = [a for a in row_dicts if a.get("sport_type") == sport]
        grade = compute_grade(r, sport_peers)
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


if __name__ == "__main__":
    webbrowser.open("http://localhost:8080")
    uvicorn.run(app, host="127.0.0.1", port=8080)
