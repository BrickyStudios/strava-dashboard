"""Made-up demo data, so the dashboard can be tried (and screenshotted) without a Strava account.

Everything here is invented: rides and runs around Innsbruck, sample coach comments, sample
segments. Nothing calls Strava, Claude or OpenStreetMap in demo mode, because surface data and
both kinds of comments are stored up front.
"""

import json
import math
import random
from datetime import date, datetime, timedelta
from pathlib import Path

from lib.db import get_conn, init_db, upsert_activity, upsert_segment_efforts

DEMO_DB = Path(__file__).parent.parent / "data" / "demo.db"
CENTER = (47.262, 11.394)  # Innsbruck
WEEKS = 26

ROUTES = [  # name, sport, km, elevation per km, start offset (lat, lon) from CENTER
    ("Inn valley gravel loop", "GravelRide", 48, 9, (0.00, 0.00)),
    ("Mieming plateau", "GravelRide", 62, 13, (0.03, -0.20)),
    ("Patscherkofel climb", "GravelRide", 34, 28, (-0.04, 0.03)),
    ("Hall after work", "Ride", 38, 5, (0.01, 0.10)),
    ("Long road day to Kufstein", "Ride", 92, 6, (0.02, 0.05)),
    ("Sill gorge trail run", "Run", 9, 22, (-0.02, 0.00)),
    ("Riverside easy run", "Run", 7, 4, (0.00, 0.02)),
]
COMMENTS = {
    "ride": [
        "Steady pacing all the way round, a strong base session.",
        "You held a higher speed than last week on the same loop, well done.",
        "Lots of climbing for the distance; your legs will thank you next month.",
        "Short and sharp. Keep these as recovery rides, not races.",
        "Your longest ride this block. Eat early on days like this.",
    ],
    "run": [
        "Nice negative split, you finished stronger than you started.",
        "Easy pace done right: this is what builds the engine.",
        "Hilly and controlled, good work on the descents.",
    ],
}
DETAIL = [
    "This was a well-paced session: speed stayed within a narrow band despite {elev} m of climbing. "
    "The first third was a little fast compared with your recent sessions, which cost you on the last climb. "
    "Next time hold back for the first 20 minutes. Your most useful tip: keep one long session like this every week.",
    "Distance and climbing are both above your 5-session average, so this counts as a key workout. "
    "Heart rate stayed moderate, which shows good aerobic fitness. "
    "Add one shorter interval session this week rather than another long day.",
]
SEGMENTS = [  # id, name, metres, best time (s), chase: rank 2–3 but never first (a "record chance")
    (9001, "Hungerburg wall", 1800, 410, False),
    (9002, "Inn bridge sprint", 600, 62, False),
    (9003, "Gnadenwald gravel", 4200, 640, False),
    (9004, "Sill gorge climb", 2500, 780, False),
    (9005, "Igls switchbacks", 3100, 655, True),
    (9006, "Rum descent to the river", 1400, 150, True),
]


def _encode(points: list[tuple[float, float]]) -> str:
    """Google encoded polyline, the format Strava uses for summary_polyline."""
    out, prev = [], (0, 0)
    for lat, lon in points:
        cur = (round(lat * 1e5), round(lon * 1e5))
        for v in (cur[0] - prev[0], cur[1] - prev[1]):
            v = ~(v << 1) if v < 0 else v << 1
            while v >= 0x20:
                out.append(chr((0x20 | (v & 0x1F)) + 63))
                v >>= 5
            out.append(chr(v + 63))
        prev = cur
    return "".join(out)


def _loop(rng: random.Random, start: tuple[float, float], km: float) -> list[tuple[float, float]]:
    """A wobbly closed loop of roughly km length."""
    r_deg = km / (2 * math.pi) / 111
    lat0, lon0 = start
    pts, n = [], 80
    phase = rng.random() * math.tau
    for i in range(n + 1):
        a = math.tau * i / n
        wobble = 1 + 0.25 * math.sin(3 * a + phase) + 0.08 * rng.random()
        pts.append((lat0 + r_deg * wobble * math.sin(a), lon0 + r_deg * wobble * math.cos(a) / 0.68))
    return pts


def build_demo_db(path: Path = DEMO_DB) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    conn = get_conn(path)
    init_db(conn)
    rng = random.Random(42)
    today = date.today()
    act_id = 1000
    for week in range(WEEKS, -1, -1):
        fitness = 1 + (WEEKS - week) * 0.006  # slowly getting faster over the half year
        for i in range(rng.choice([2, 3, 3, 4])):
            # one gravel ride this morning, so "this week" is never empty in screenshots
            first_today = week == 0 and i == 0
            name, sport, km, hm_per_km, off = ROUTES[1] if first_today else rng.choice(ROUTES)
            day = today if first_today else today - timedelta(weeks=week, days=rng.randint(0, 6))
            if day > today:
                continue
            act_id += 1
            dist = km * 1000 * rng.uniform(0.9, 1.1)
            elev = dist / 1000 * hm_per_km * rng.uniform(0.85, 1.15)
            base = {"GravelRide": 6.1, "Ride": 7.6, "Run": 3.05}[sport]
            speed = base * fitness * rng.uniform(0.94, 1.06) - elev / dist * 4
            moving = int(dist / speed)
            hr = rng.uniform(132, 158)
            start = (CENTER[0] + off[0], CENTER[1] + off[1])
            pts = _loop(rng, start, dist / 1000)
            hour = 7 if day == today else rng.choice([7, 9, 16, 17, 18])
            when = datetime(day.year, day.month, day.day, hour, rng.randint(0, 59))
            upsert_activity(conn, {
                "id": act_id, "name": name, "sport_type": sport,
                "start_date": when.isoformat() + "Z", "start_date_local": when.isoformat(),
                "distance": round(dist, 1), "moving_time": moving, "elapsed_time": int(moving * rng.uniform(1.03, 1.15)),
                "total_elevation_gain": round(elev), "average_speed": round(speed, 3),
                "max_speed": round(speed * rng.uniform(1.8, 2.4), 2), "average_heartrate": round(hr),
                "max_heartrate": round(hr + rng.uniform(20, 32)),
                "kilojoules": round(moving * rng.uniform(0.16, 0.2)) if sport != "Run" else None,
                "average_watts": round(rng.uniform(150, 205)) if sport != "Run" else None,
                "elev_low": 560, "elev_high": round(560 + elev * 0.45),
                "pr_count": rng.choice([0, 0, 0, 1, 2]), "achievement_count": rng.choice([0, 1, 3]),
                "map": {"summary_polyline": _encode(pts)},
            })
            mix = {"GravelRide": (0.35, 0.5, 0.15), "Ride": (0.92, 0.08, 0.0), "Run": (0.2, 0.3, 0.5)}[sport]
            surface = {"asphalt_m": round(dist * mix[0]), "gravel_m": round(dist * mix[1]),
                       "trail_m": round(dist * mix[2]), "unknown_m": 0, "total_analyzed_m": round(dist)}
            conn.execute(
                "UPDATE activities SET ai_comment = ?, detail_comment = ?, surface_json = ? WHERE id = ?",
                (rng.choice(COMMENTS['run' if sport == 'Run' else 'ride']), rng.choice(DETAIL).format(elev=round(elev)), json.dumps(surface), act_id),
            )
            efforts = []
            for seg_id, seg_name, seg_m, best, chase in rng.sample(SEGMENTS, 2):
                t = int(best * rng.uniform(1.0, 1.25) / fitness)
                if chase:
                    pr, overall = rng.choice([None, 2, 3]), rng.choice([2, 3, 5, None])
                else:
                    pr = 1 if t < best * 1.04 else rng.choice([None, 2, 3])
                    overall = rng.choice([None, None, 1, 2, 4, 7]) if t < best * 1.08 else None
                efforts.append({"segment_id": seg_id, "segment_name": seg_name, "segment_distance_m": seg_m,
                                "elapsed_time_s": t, "start_date_local": when.isoformat(),
                                "pr_rank": pr, "overall_rank": overall})
            upsert_segment_efforts(conn, act_id, efforts)
    conn.commit()
    conn.close()
