"""Generate and cache AI coaching comments via Claude Haiku."""

import logging
import sqlite3
import anthropic
from pathlib import Path

from dotenv import dotenv_values

from lib.grade import compute_grade
from lib.db import get_activities

ENV_PATH = Path(__file__).parent.parent / ".env"
MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 80

logger = logging.getLogger(__name__)


def _get_api_key() -> str:
    return dotenv_values(ENV_PATH).get("ANTHROPIC_API_KEY", "")


def generate_comment(activity: dict, grade: str, client: anthropic.Anthropic) -> str | None:
    dist_km = (activity.get("distance_m") or 0) / 1000
    duration_min = (activity.get("moving_time_s") or 0) / 60
    speed_kmh = (activity.get("avg_speed_ms") or 0) * 3.6
    elev = activity.get("elevation_gain_m") or 0

    prompt = (
        f"Du bist ein erfahrener Radtrainer. Gib einen kurzen, motivierenden Kommentar "
        f"(1–2 Sätze, Deutsch, Du-Form) zu dieser Einheit:\n\n"
        f"Sport: {activity.get('sport_type', 'Unbekannt')}\n"
        f"Name: {activity.get('name', '')}\n"
        f"Distanz: {dist_km:.1f} km\n"
        f"Dauer: {duration_min:.0f} min\n"
        f"Durchschnittstempo: {speed_kmh:.1f} km/h\n"
        f"Höhenmeter: {elev:.0f} m\n"
        f"Note: {grade}\n\n"
        f"Nur der Kommentar, keine Einleitung."
    )

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()
    except Exception as e:
        logger.warning("AI comment generation failed for activity %s: %s", activity.get("id"), e)
        return None


def generate_missing_comments(conn: sqlite3.Connection) -> None:
    """Generate and store comments for activities that have none. Max 20 per call."""
    api_key = _get_api_key()
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY is not set; skipping AI comment generation")
        return

    client = anthropic.Anthropic(api_key=api_key)

    rows = conn.execute(
        "SELECT * FROM activities WHERE ai_comment IS NULL "
        "ORDER BY start_date_local DESC LIMIT 20"
    ).fetchall()

    sport_cache: dict[str, list] = {}

    for row in rows:
        activity = dict(row)
        sport = activity.get("sport_type")
        if sport not in sport_cache:
            sport_cache[sport] = get_activities(conn, sport_type=sport)
        all_same_sport = sport_cache[sport]
        grade = compute_grade(activity, [dict(a) for a in all_same_sport])
        comment = generate_comment(activity, grade, client)
        if comment is not None:
            conn.execute(
                "UPDATE activities SET ai_comment = ? WHERE id = ?",
                (comment, activity["id"]),
            )
            conn.commit()
