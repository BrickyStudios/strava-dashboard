"""Tests for the 3 analytics MCP tools. Uses in-memory DB from conftest."""
import pytest
from unittest.mock import patch


def test_get_performance_trend_returns_table(db_with_activities):
    with patch("main.get_conn", return_value=db_with_activities):
        from main import get_performance_trend
        result = get_performance_trend(sport_type="GravelRide", metric="avg_speed_kmh", weeks=4)
    assert "Week" in result
    assert "km/h" in result
    assert "2026-W" in result


def test_get_performance_trend_speed_is_distance_weighted(db_with_activities):
    with patch("main.get_conn", return_value=db_with_activities):
        from main import get_performance_trend
        result = get_performance_trend(sport_type="GravelRide", metric="avg_speed_kmh", weeks=4)
    assert "23." in result or "19." in result


def test_get_performance_trend_includes_empty_weeks(db_with_activities):
    with patch("main.get_conn", return_value=db_with_activities):
        from main import get_performance_trend
        result = get_performance_trend(sport_type="GravelRide", metric="avg_speed_kmh", weeks=12)
    assert "—" in result


def test_get_weekly_volume_returns_table(db_with_activities):
    with patch("main.get_conn", return_value=db_with_activities):
        from main import get_weekly_volume
        result = get_weekly_volume(sport_type="GravelRide", weeks=4)
    assert "km" in result
    assert "Elevation" in result
    assert "2026-W" in result


def test_get_weekly_volume_all_sports(db_with_activities):
    with patch("main.get_conn", return_value=db_with_activities):
        from main import get_weekly_volume
        result = get_weekly_volume(sport_type=None, weeks=4)
    assert "5." in result or "80." in result


def test_get_personal_bests_returns_all_fields(db_with_activities):
    with patch("main.get_conn", return_value=db_with_activities):
        from main import get_personal_bests
        result = get_personal_bests(sport_type="GravelRide")
    assert "longest_distance" in result
    assert "longest_duration" in result
    assert "fastest" in result
    assert "elevation" in result
    assert "75." in result


def test_get_personal_bests_sport_filter(db_with_activities):
    with patch("main.get_conn", return_value=db_with_activities):
        from main import get_personal_bests
        result = get_personal_bests(sport_type="Run")
    assert "5." in result
