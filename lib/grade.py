"""Composite activity grading: A+ / A / B+ / B / C."""

COLD_START_MIN = 5

COLD_START_THRESHOLDS = {
    "GravelRide": [60_000, 40_000, 25_000, 10_000],
    "Run":        [15_000, 10_000,  7_000,  5_000],
    "_default":   [20_000, 12_000,  8_000,  4_000],
}
GRADE_LABELS = ["A+", "A", "B+", "B", "C"]


def percentile_rank(value: float, values: list[float]) -> float:
    """Fraction of values strictly less than value (0.0–1.0)."""
    if not values:
        return 0.0
    return sum(1 for v in values if v < value) / len(values)


def _eas(activity: dict) -> float:
    speed_kmh = (activity.get("avg_speed_ms") or 0) * 3.6
    dist_km = (activity.get("distance_m") or 0) / 1000
    elev = activity.get("elevation_gain_m") or 0
    hm_per_km = elev / dist_km if dist_km > 0 else 0
    return speed_kmh + hm_per_km * 0.04


def _cold_start_grade(activity: dict) -> str:
    sport = activity.get("sport_type", "_default")
    thresholds = COLD_START_THRESHOLDS.get(sport, COLD_START_THRESHOLDS["_default"])
    dist = activity.get("distance_m") or 0
    for i, threshold in enumerate(thresholds):
        if dist >= threshold:
            return GRADE_LABELS[i]
    return "C"


def _score_to_grade(score: float) -> str:
    if score >= 85:
        return "A+"
    if score >= 70:
        return "A"
    if score >= 55:
        return "B+"
    if score >= 40:
        return "B"
    return "C"


def compute_grade(activity: dict, all_activities: list[dict]) -> str:
    """
    activity: dict with distance_m, moving_time_s, elevation_gain_m, avg_speed_ms, sport_type
    all_activities: other activities of the same sport_type (may include self — will be excluded)
    """
    others = [a for a in all_activities if a.get("id") != activity.get("id")]

    if len(others) < COLD_START_MIN:
        return _cold_start_grade(activity)

    distances = [a.get("distance_m") or 0 for a in others]
    eas_values = [_eas(a) for a in others]
    elevations = [a.get("elevation_gain_m") or 0 for a in others]

    dist_score = percentile_rank(activity.get("distance_m") or 0, distances) * 100
    eas_score  = percentile_rank(_eas(activity), eas_values) * 100
    elev_score = percentile_rank(activity.get("elevation_gain_m") or 0, elevations) * 100

    composite = 0.50 * dist_score + 0.30 * eas_score + 0.20 * elev_score
    return _score_to_grade(composite)
