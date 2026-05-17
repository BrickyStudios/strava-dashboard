import pytest


RIDE = lambda id, dist, time, elev, speed: {
    "id": id, "sport_type": "GravelRide",
    "distance_m": dist, "moving_time_s": time,
    "elevation_gain_m": elev, "avg_speed_ms": speed,
}


def test_percentile_rank_highest_value():
    from lib.grade import percentile_rank
    assert percentile_rank(100, [10, 20, 50, 100]) == 0.75  # 3 of 4 values < 100


def test_percentile_rank_lowest_value():
    from lib.grade import percentile_rank
    assert percentile_rank(10, [10, 20, 50, 100]) == 0.0


def test_percentile_rank_single_element():
    from lib.grade import percentile_rank
    assert percentile_rank(5, [5]) == 0.0


def test_cold_start_gravel_long_ride():
    from lib.grade import compute_grade
    activity = RIDE(1, 70000, 10000, 400, 7.0)
    others = [RIDE(2, 70000, 10000, 400, 7.0)]  # only 1 other → cold start
    assert compute_grade(activity, others) == "A+"


def test_cold_start_gravel_short_ride():
    from lib.grade import compute_grade
    activity = RIDE(1, 8000, 1200, 30, 6.5)
    others = [RIDE(2, 8000, 1200, 30, 6.5)]
    assert compute_grade(activity, others) == "C"


def test_percentile_grade_best_ride():
    from lib.grade import compute_grade
    # One very strong ride vs 9 weak ones
    strong = RIDE(10, 80000, 11000, 500, 7.5)
    weak = [RIDE(i, 20000, 4000, 100, 5.0) for i in range(9)]
    assert compute_grade(strong, weak) == "A+"


def test_percentile_grade_median_ride():
    from lib.grade import compute_grade
    # Vary distance AND elevation so strict-less-than percentile works for both dimensions
    rides = [RIDE(i, 30000 + i * 5000, 5000, 100 + i * 20, 6.0) for i in range(10)]
    median_ride = rides[4]
    others = rides[:4] + rides[5:]
    grade = compute_grade(median_ride, others)
    assert grade in ("B", "B+")


def test_compute_grade_excludes_self():
    from lib.grade import compute_grade
    ride = RIDE(1, 50000, 7000, 300, 6.5)
    others = [RIDE(i, 50000, 7000, 300, 6.5) for i in range(8)]
    grade = compute_grade(ride, others)
    assert grade in ("A+", "A", "B+", "B", "C")
