import pytest
import sqlite3
from unittest.mock import patch, MagicMock
from lib.db import init_db, upsert_activity


ACTIVITY = {
    "id": 1,
    "name": "Seen Runde",
    "sport_type": "GravelRide",
    "start_date": "2026-05-16T07:00:00Z",
    "start_date_local": "2026-05-16T09:00:00",
    "distance": 75300.0,
    "moving_time": 11580,
    "total_elevation_gain": 417.0,
    "average_speed": 6.503,
    "max_speed": 18.0,
    "average_heartrate": None,
    "kilojoules": 1376.0,
}


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    upsert_activity(conn, ACTIVITY)
    yield conn
    conn.close()


def _mock_client(text="Tolle Fahrt! Dein bestes Ergebnis diese Woche."):
    mock = MagicMock()
    mock.messages.create.return_value.content = [MagicMock(text=text)]
    return mock


def test_generate_comment_returns_string():
    from lib.ai_coach import generate_comment
    row = dict(ACTIVITY)
    row["distance_m"] = row.pop("distance")
    row["moving_time_s"] = row.pop("moving_time")
    row["elevation_gain_m"] = row.pop("total_elevation_gain")
    row["avg_speed_ms"] = row.pop("average_speed")
    client = _mock_client()
    result = generate_comment(row, "A+", client)
    assert isinstance(result, str)
    assert len(result) > 5


def test_generate_comment_returns_none_on_api_error():
    from lib.ai_coach import generate_comment
    row = {"id": 1, "name": "Test", "sport_type": "GravelRide",
           "distance_m": 40000, "moving_time_s": 6000,
           "elevation_gain_m": 200, "avg_speed_ms": 6.5}
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = Exception("API error")
    result = generate_comment(row, "B", mock_client)
    assert result is None


def test_generate_missing_comments_fills_nulls(db):
    from lib.ai_coach import generate_missing_comments
    with patch("lib.ai_coach._get_api_key", return_value="test-key"), \
         patch("lib.ai_coach.anthropic.Anthropic", return_value=_mock_client()), \
         patch("lib.ai_coach.generate_comment", return_value="Starke Leistung!"):
        generate_missing_comments(db)
    row = db.execute("SELECT ai_comment FROM activities WHERE id = 1").fetchone()
    assert row[0] == "Starke Leistung!"


def test_generate_missing_comments_skips_existing(db):
    db.execute("UPDATE activities SET ai_comment = 'Already set' WHERE id = 1")
    db.commit()
    from lib.ai_coach import generate_missing_comments
    with patch("lib.ai_coach._get_api_key", return_value="test-key"), \
         patch("lib.ai_coach.anthropic.Anthropic", return_value=_mock_client()), \
         patch("lib.ai_coach.generate_comment") as mock_gen:
        generate_missing_comments(db)
    mock_gen.assert_not_called()


def test_generate_missing_comments_skips_on_none_response(db):
    from lib.ai_coach import generate_missing_comments
    with patch("lib.ai_coach._get_api_key", return_value="test-key"), \
         patch("lib.ai_coach.anthropic.Anthropic", return_value=_mock_client()), \
         patch("lib.ai_coach.generate_comment", return_value=None):
        generate_missing_comments(db)
    row = db.execute("SELECT ai_comment FROM activities WHERE id = 1").fetchone()
    assert row[0] is None  # still NULL, not written
