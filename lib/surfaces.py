"""Surface type analysis for GPS routes via OSM Overpass API."""

import math
import time
import httpx

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = "strava-local-dashboard/1.0 (personal training tool)"

OSM_SURFACE_MAP: dict[str, str] = {
    # asphalt
    "asphalt": "asphalt", "paved": "asphalt", "concrete": "asphalt",
    "concrete:plates": "asphalt", "cobblestone": "asphalt", "sett": "asphalt",
    "metal": "asphalt", "tarred": "asphalt",
    # gravel
    "gravel": "gravel", "fine_gravel": "gravel", "compacted": "gravel",
    "unpaved": "gravel", "ground": "gravel", "dirt": "gravel",
    "pebblestone": "gravel", "grass_paver": "gravel", "sand": "gravel",
    "wood": "gravel", "woodchips": "gravel",
    # trail
    "grass": "trail", "earth": "trail", "mud": "trail",
    "woodchip": "trail", "rock": "trail", "stone": "trail",
}

OSM_HIGHWAY_DEFAULT: dict[str, str] = {
    "motorway": "asphalt", "trunk": "asphalt", "primary": "asphalt",
    "secondary": "asphalt", "tertiary": "asphalt", "unclassified": "asphalt",
    "residential": "asphalt", "service": "asphalt", "cycleway": "asphalt",
    "living_street": "asphalt", "pedestrian": "asphalt", "road": "asphalt",
    "track": "gravel",
    "path": "trail", "footway": "trail", "bridleway": "trail", "steps": "trail",
}


def decode_polyline(encoded: str) -> list[tuple[float, float]]:
    coords: list[tuple[float, float]] = []
    idx = lat = lng = 0
    while idx < len(encoded):
        b = shift = result = 0
        while True:
            b = ord(encoded[idx]) - 63
            idx += 1
            result |= (b & 0x1f) << shift
            shift += 5
            if b < 0x20:
                break
        lat += ~(result >> 1) if (result & 1) else (result >> 1)
        b = shift = result = 0
        while True:
            b = ord(encoded[idx]) - 63
            idx += 1
            result |= (b & 0x1f) << shift
            shift += 5
            if b < 0x20:
                break
        lng += ~(result >> 1) if (result & 1) else (result >> 1)
        coords.append((lat / 1e5, lng / 1e5))
    return coords


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371000.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlng / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def _classify_way(tags: dict) -> str:
    surface = tags.get("surface", "")
    if surface in OSM_SURFACE_MAP:
        return OSM_SURFACE_MAP[surface]
    return OSM_HIGHWAY_DEFAULT.get(tags.get("highway", ""), "unknown")


def _point_to_segment_dist_deg(px: float, py: float,
                                ax: float, ay: float,
                                bx: float, by: float) -> float:
    dx, dy = bx - ax, by - ay
    if dx == 0.0 and dy == 0.0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def analyze_surfaces(polyline_str: str) -> dict:
    """Return surface breakdown in metres. Returns {} on Overpass failure."""
    coords = decode_polyline(polyline_str)
    if len(coords) < 2:
        return {"no_route": True}

    # Downsample: every 8th point, always include first + last
    step = max(1, len(coords) // 200)
    indices = list(range(0, len(coords), step))
    if indices[-1] != len(coords) - 1:
        indices.append(len(coords) - 1)
    sampled = [coords[i] for i in indices]

    lats = [c[0] for c in sampled]
    lngs = [c[1] for c in sampled]
    pad = 0.003  # ~300 m
    bbox = f"{min(lats)-pad},{min(lngs)-pad},{max(lats)+pad},{max(lngs)+pad}"

    query = f'[out:json][timeout:30];way["highway"]({bbox});out body;>;out skel qt;'

    try:
        resp = httpx.post(
            OVERPASS_URL,
            data={"data": query},
            headers={"User-Agent": USER_AGENT},
            timeout=45.0,
        )
        resp.raise_for_status()
        osm = resp.json()
    except Exception as e:
        print(f"  OSM query failed: {e}")
        return {}  # NULL → will be retried on next sync

    nodes: dict[int, tuple[float, float]] = {}
    for el in osm.get("elements", []):
        if el["type"] == "node":
            nodes[el["id"]] = (el["lat"], el["lon"])

    # List of (lat1, lng1, lat2, lng2, surface_type)
    way_segs: list[tuple[float, float, float, float, str]] = []
    for el in osm.get("elements", []):
        if el["type"] != "way":
            continue
        surface_type = _classify_way(el.get("tags") or {})
        nd = el.get("nodes", [])
        for i in range(len(nd) - 1):
            if nd[i] in nodes and nd[i + 1] in nodes:
                a = nodes[nd[i]]
                b = nodes[nd[i + 1]]
                way_segs.append((a[0], a[1], b[0], b[1], surface_type))

    if not way_segs:
        return {"no_route": True}  # No OSM data → don't retry

    # Threshold: 100 m in degree space (latitude accurate, lng ~1.5x at 50°N)
    threshold_deg = 100.0 / 111_000.0

    buckets: dict[str, float] = {"asphalt": 0.0, "gravel": 0.0, "trail": 0.0, "unknown": 0.0}
    total_analyzed = 0.0

    for i in range(len(sampled) - 1):
        lat1, lng1 = sampled[i]
        lat2, lng2 = sampled[i + 1]
        mid_lat = (lat1 + lat2) / 2.0
        mid_lng = (lng1 + lng2) / 2.0
        seg_dist = _haversine_m(lat1, lng1, lat2, lng2)

        best_dist = float("inf")
        best_surface = "unknown"
        for wl1, wg1, wl2, wg2, wsurf in way_segs:
            d = _point_to_segment_dist_deg(mid_lat, mid_lng, wl1, wg1, wl2, wg2)
            if d < best_dist:
                best_dist = d
                best_surface = wsurf

        if best_dist <= threshold_deg:
            buckets[best_surface] += seg_dist
            total_analyzed += seg_dist

    if total_analyzed == 0.0:
        return {"no_route": True}

    return {
        "asphalt_m": round(buckets["asphalt"]),
        "gravel_m": round(buckets["gravel"]),
        "trail_m": round(buckets["trail"]),
        "unknown_m": round(buckets["unknown"]),
        "total_analyzed_m": round(total_analyzed),
    }


def sync_surfaces(conn, delay_s: float = 1.5) -> None:
    """Analyze surfaces for activities that haven't been processed yet."""
    import json as _json

    rows = conn.execute("""
        SELECT id, raw_json FROM activities
        WHERE surface_json IS NULL
        ORDER BY start_date_local DESC
    """).fetchall()

    if not rows:
        print("All activities already have surface data.")
        return

    print(f"Analyzing surfaces for {len(rows)} activities via OSM...")
    for row in rows:
        activity_id = row[0]
        raw = _json.loads(row[1] or "{}")
        polyline = (raw.get("map") or {}).get("summary_polyline")

        if not polyline:
            print(f"  Activity {activity_id}: no polyline, skipping")
            conn.execute(
                "UPDATE activities SET surface_json = ? WHERE id = ?",
                ('{"no_route": true}', activity_id),
            )
            conn.commit()
            continue

        result = analyze_surfaces(polyline)
        if not result:
            # Overpass failed — leave NULL to retry next time
            print(f"  Activity {activity_id}: Overpass failed, will retry later")
            time.sleep(delay_s)
            continue

        conn.execute(
            "UPDATE activities SET surface_json = ? WHERE id = ?",
            (_json.dumps(result), activity_id),
        )
        conn.commit()
        total = result.get("total_analyzed_m", 0)
        asphalt_pct = round(result.get("asphalt_m", 0) / total * 100) if total else 0
        gravel_pct = round(result.get("gravel_m", 0) / total * 100) if total else 0
        trail_pct = round(result.get("trail_m", 0) / total * 100) if total else 0
        print(f"  Activity {activity_id}: Asphalt {asphalt_pct}% / Schotter {gravel_pct}% / Trail {trail_pct}%")
        time.sleep(delay_s)
