import time
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path


def make_response(activities, headers=None):
    mock = MagicMock()
    mock.json.return_value = activities
    mock.headers = headers or {"X-RateLimit-Limit": "100,1000", "X-RateLimit-Usage": "1,1"}
    mock.raise_for_status.return_value = None
    return mock


def test_refresh_tokens_writes_back_to_env(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "STRAVA_CLIENT_ID=123\n"
        "STRAVA_CLIENT_SECRET=secret\n"
        "STRAVA_ACCESS_TOKEN=old_access\n"
        "STRAVA_REFRESH_TOKEN=old_refresh\n"
    )

    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "access_token": "new_access",
        "refresh_token": "new_refresh",
    }
    mock_resp.raise_for_status.return_value = None

    with patch("httpx.post", return_value=mock_resp):
        from sync import refresh_tokens
        token = refresh_tokens(env_path=env_file)

    assert token == "new_access"
    content = env_file.read_text()
    assert "new_access" in content
    assert "new_refresh" in content


def test_check_rate_limit_sleeps_when_approaching_15min_limit():
    headers = {"X-RateLimit-Limit": "100,1000", "X-RateLimit-Usage": "91,200"}
    with patch("time.sleep") as mock_sleep:
        from sync import check_rate_limit
        check_rate_limit(headers)
        mock_sleep.assert_called_once()
        sleep_seconds = mock_sleep.call_args[0][0]
        assert sleep_seconds > 0


def test_check_rate_limit_raises_on_daily_limit():
    headers = {"X-RateLimit-Limit": "100,1000", "X-RateLimit-Usage": "5,995"}
    from sync import DailyLimitReached, check_rate_limit
    with pytest.raises(DailyLimitReached):
        check_rate_limit(headers)


def test_check_rate_limit_passes_when_under_limit():
    headers = {"X-RateLimit-Limit": "100,1000", "X-RateLimit-Usage": "10,100"}
    from sync import check_rate_limit
    check_rate_limit(headers)  # should not raise or sleep


def test_sync_upserts_activities_into_db(db):
    sample = [
        {
            "id": 42,
            "name": "Test Ride",
            "sport_type": "GravelRide",
            "type": "GravelRide",
            "start_date": "2026-05-01T08:00:00Z",
            "start_date_local": "2026-05-01T10:00:00",
            "distance": 50000.0,
            "moving_time": 7200,
            "total_elevation_gain": 300.0,
            "average_speed": 6.944,
            "max_speed": 15.0,
            "average_heartrate": None,
            "kilojoules": 900.0,
        }
    ]

    page1 = make_response(sample)
    page2 = make_response([])

    with patch("httpx.get", side_effect=[page1, page2]):
        with patch("sync.refresh_tokens", return_value="fake_token"):
            from sync import run_sync
            run_sync(conn=db, full=False, last_epoch=0)

    from lib.db import get_activities
    rows = get_activities(db)
    assert len(rows) == 1
    assert rows[0]["id"] == 42


def test_sync_calls_generate_missing_comments(tmp_path):
    """After a successful sync, generate_missing_comments is called."""
    from sync import main
    env_file = tmp_path / ".env"
    env_file.write_text(
        "STRAVA_CLIENT_ID=1\nSTRAVA_CLIENT_SECRET=s\n"
        "STRAVA_ACCESS_TOKEN=t\nSTRAVA_REFRESH_TOKEN=r\n"
    )
    mock_resp = MagicMock()
    mock_resp.json.side_effect = [
        [{"id": 99, "name": "Test", "sport_type": "GravelRide",
          "start_date": "2026-05-01T10:00:00Z", "start_date_local": "2026-05-01T12:00:00",
          "distance": 40000.0, "moving_time": 6000, "total_elevation_gain": 200.0,
          "average_speed": 6.5, "max_speed": 15.0, "kilojoules": 700.0}],
        [],  # second page → empty → stop
    ]
    mock_resp.headers = {"X-RateLimit-Limit": "100,1000", "X-RateLimit-Usage": "1,1"}
    mock_resp.raise_for_status.return_value = None

    mock_token_resp = MagicMock()
    mock_token_resp.json.return_value = {"access_token": "new", "refresh_token": "new_r"}
    mock_token_resp.raise_for_status.return_value = None

    with patch("sys.argv", ["sync.py"]), \
         patch("httpx.post", return_value=mock_token_resp), \
         patch("httpx.get", return_value=mock_resp), \
         patch("sync.ENV_PATH", env_file), \
         patch("sync.generate_missing_comments") as mock_gen_comments, \
         patch("sync.sync_segment_efforts"):
        main()

    mock_gen_comments.assert_called_once()


# --- parse_segment_effort tests ---

def test_parse_segment_effort_with_kom():
    from sync import parse_segment_effort
    raw = {
        "elapsed_time": 54,
        "start_date_local": "2026-05-19T10:30:00Z",
        "pr_rank": 1,
        "segment": {"id": 999, "name": "Bergkuppe Sprint", "distance": 420.5},
        "achievements": [{"type": "overall", "type_id": 2, "rank": 1}],
    }
    result = parse_segment_effort(raw)
    assert result["segment_id"] == 999
    assert result["segment_name"] == "Bergkuppe Sprint"
    assert result["segment_distance_m"] == 420.5
    assert result["elapsed_time_s"] == 54
    assert result["overall_rank"] == 1
    assert result["pr_rank"] == 1


def test_parse_segment_effort_no_achievement():
    from sync import parse_segment_effort
    raw = {
        "elapsed_time": 120,
        "start_date_local": "2026-05-01T09:00:00Z",
        "pr_rank": None,
        "segment": {"id": 777, "name": "Flat Road", "distance": 1000.0},
        "achievements": [],
    }
    result = parse_segment_effort(raw)
    assert result["segment_id"] == 777
    assert result["overall_rank"] is None


def test_parse_segment_effort_rank2_skips_non_overall():
    from sync import parse_segment_effort
    raw = {
        "elapsed_time": 198,
        "start_date_local": "2026-05-10T08:00:00Z",
        "pr_rank": 1,
        "segment": {"id": 888, "name": "Waldweg Abfahrt", "distance": 1200.0},
        "achievements": [
            {"type": "year_overall", "type_id": 3, "rank": 1},
            {"type": "overall", "type_id": 2, "rank": 2},
        ],
    }
    result = parse_segment_effort(raw)
    assert result["overall_rank"] == 2
