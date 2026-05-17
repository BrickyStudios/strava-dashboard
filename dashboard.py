"""Local performance dashboard — FastAPI + Chart.js at localhost:8080."""

import webbrowser
from datetime import date, timedelta
from typing import Optional

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from lib.db import get_conn, init_db, get_activities

app = FastAPI()


def _iso_week_label(d: date) -> str:
    iso = d.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _trailing_weeks(n: int) -> list[str]:
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    return [_iso_week_label(monday - timedelta(weeks=i)) for i in range(n - 1, -1, -1)]


@app.get("/api/data")
def api_data(sport_type: Optional[str] = None, weeks: int = 12):
    conn = get_conn()
    init_db(conn)
    rows = get_activities(conn, sport_type=sport_type or None)

    buckets: dict[str, dict] = {}
    for row in rows:
        label = _iso_week_label(date.fromisoformat(row["start_date_local"][:10]))
        if label not in buckets:
            buckets[label] = {"dist": 0.0, "time": 0, "elev": 0.0}
        buckets[label]["dist"] += row["distance_m"] or 0
        buckets[label]["time"] += row["moving_time_s"] or 0
        buckets[label]["elev"] += row["elevation_gain_m"] or 0

    labels = _trailing_weeks(weeks)
    speed, volume_km, eas = [], [], []

    for label in labels:
        b = buckets.get(label)
        if b is None or b["time"] == 0:
            speed.append(None)
            volume_km.append(None)
            eas.append(None)
        else:
            avg_spd = b["dist"] / b["time"] * 3.6
            hm_per_km = b["elev"] / (b["dist"] / 1000) if b["dist"] > 0 else 0
            speed.append(round(avg_spd, 2))
            volume_km.append(round(b["dist"] / 1000, 1))
            eas.append(round(avg_spd + hm_per_km * 0.04, 2))

    return {"labels": labels, "speed": speed, "volume_km": volume_km, "eas": eas}


@app.get("/", response_class=HTMLResponse)
def root():
    conn = get_conn()
    init_db(conn)
    count = conn.execute("SELECT COUNT(*) FROM activities").fetchone()[0]

    if count == 0:
        return HTMLResponse("""
<!DOCTYPE html><html><body style="font-family:sans-serif;display:flex;
justify-content:center;align-items:center;height:100vh;margin:0;background:#f5f5f5">
<div style="text-align:center;padding:2rem;background:#fff;border-radius:8px;box-shadow:0 2px 8px rgba(0,0,0,.1)">
  <h2>No data yet</h2>
  <p>Run: <code style="background:#f0f0f0;padding:.2rem .5rem;border-radius:4px">uv run sync.py</code></p>
</div></body></html>""")

    return HTMLResponse("""
<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8">
  <title>Strava Performance Dashboard</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
  <style>
    * { box-sizing: border-box; }
    body { font-family: system-ui, sans-serif; margin: 0; padding: 1.5rem; background: #f5f5f5; }
    h1 { margin: 0 0 1rem; font-size: 1.4rem; }
    .controls { display: flex; gap: 1rem; margin-bottom: 1.5rem; align-items: center; }
    select { padding: .4rem .8rem; border: 1px solid #ccc; border-radius: 6px; font-size: 1rem; }
    .charts { display: grid; gap: 1.5rem; }
    .card { background: #fff; border-radius: 8px; padding: 1.25rem; box-shadow: 0 1px 4px rgba(0,0,0,.08); }
    .card h3 { margin: 0 0 1rem; font-size: 1rem; color: #333; }
    canvas { max-height: 240px; }
  </style>
</head>
<body>
  <h1>Gravel Performance Dashboard</h1>
  <div class="controls">
    <select id="sport">
      <option value="">Alle</option>
      <option value="GravelRide" selected>Gravel</option>
      <option value="Run">Laufen</option>
    </select>
    <select id="weeks">
      <option value="4">4 Wochen</option>
      <option value="12" selected>12 Wochen</option>
      <option value="26">26 Wochen</option>
      <option value="52">52 Wochen</option>
    </select>
  </div>
  <div class="charts">
    <div class="card"><h3>Avg Tempo pro Woche (km/h)</h3><canvas id="c1"></canvas></div>
    <div class="card"><h3>Distanz pro Woche (km)</h3><canvas id="c2"></canvas></div>
    <div class="card"><h3>Elevation Adjusted Speed (km/h)</h3><canvas id="c3"></canvas></div>
  </div>
  <script>
    const COLORS = { line: '#3b82f6', bar: '#10b981', eas: '#8b5cf6' };
    const cfg = (labels, data, type, color) => ({
      type, data: {
        labels,
        datasets: [{ data, borderColor: color, backgroundColor: type === 'bar' ? color + '99' : color,
          fill: false, tension: 0.3, spanGaps: false, pointRadius: 3 }]
      },
      options: { responsive: true, plugins: { legend: { display: false } },
        scales: { y: { beginAtZero: false } } }
    });

    let charts = [];
    async function load() {
      charts.forEach(c => c.destroy()); charts = [];
      const sport = document.getElementById('sport').value;
      const weeks = document.getElementById('weeks').value;
      const r = await fetch(`/api/data?sport_type=${sport}&weeks=${weeks}`);
      const d = await r.json();
      charts.push(new Chart('c1', cfg(d.labels, d.speed, 'line', COLORS.line)));
      charts.push(new Chart('c2', cfg(d.labels, d.volume_km, 'bar', COLORS.bar)));
      charts.push(new Chart('c3', cfg(d.labels, d.eas, 'line', COLORS.eas)));
    }
    document.getElementById('sport').addEventListener('change', load);
    document.getElementById('weeks').addEventListener('change', load);
    load();
  </script>
</body>
</html>
""")


if __name__ == "__main__":
    webbrowser.open("http://localhost:8080")
    uvicorn.run(app, host="127.0.0.1", port=8080)
