"""Demo mode must work offline: data, routes, comments and surfaces are all stored up front."""
import json

from lib.db import get_conn
from lib.demo import CENTER, _encode, build_demo_db
from lib.surfaces import decode_polyline


def test_polyline_roundtrip():
    pts = [(47.26201, 11.39402), (47.27, 11.38), (47.255, 11.401)]
    back = decode_polyline(_encode(pts))
    assert len(back) == 3
    for (a, b), (c, d) in zip(pts, back):
        assert abs(a - c) < 1e-5 and abs(b - d) < 1e-5


def test_demo_db_is_complete(tmp_path):
    path = tmp_path / "demo.db"
    build_demo_db(path)
    conn = get_conn(path)
    rows = conn.execute("SELECT * FROM activities").fetchall()
    assert len(rows) > 50
    assert {r["sport_type"] for r in rows} == {"GravelRide", "Ride", "Run"}
    for r in rows:
        assert r["ai_comment"] and r["detail_comment"]
        assert json.loads(r["surface_json"])["total_analyzed_m"] > 0
        lat, lon = decode_polyline(json.loads(r["raw_json"])["map"]["summary_polyline"])[0]
        assert abs(lat - CENTER[0]) < 0.3 and abs(lon - CENTER[1]) < 0.5
    assert conn.execute("SELECT COUNT(*) FROM segment_efforts WHERE overall_rank = 1").fetchone()[0] > 0
