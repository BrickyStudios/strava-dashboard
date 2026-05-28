"""Generate and cache AI coaching comments via Claude."""

import json
import logging
import sqlite3
import anthropic
from pathlib import Path

from dotenv import dotenv_values

from lib.grade import compute_grade
from lib.db import get_activities

ENV_PATH = Path(__file__).parent.parent / ".env"
MODEL_SHORT = "claude-haiku-4-5-20251001"
MODEL_DETAIL = "claude-sonnet-4-6"
MAX_TOKENS = 100
MAX_TOKENS_DETAIL = 400

logger = logging.getLogger(__name__)


def _get_api_key() -> str:
    return dotenv_values(ENV_PATH).get("ANTHROPIC_API_KEY", "")


def _enrich_from_raw(activity: dict) -> dict:
    """Parse raw_json and add extra fields to activity dict."""
    raw = json.loads(activity.get("raw_json") or "{}")
    return {
        **activity,
        "elev_high_m": raw.get("elev_high"),
        "elev_low_m": raw.get("elev_low"),
        "avg_watts": raw.get("average_watts"),
        "pr_count": raw.get("pr_count") or 0,
        "achievement_count": raw.get("achievement_count") or 0,
        "trainer": raw.get("trainer", False),
        "commute": raw.get("commute", False),
    }


def generate_comment(activity: dict, grade: str, client: anthropic.Anthropic) -> str | None:
    dist_km = (activity.get("distance_m") or 0) / 1000
    duration_min = (activity.get("moving_time_s") or 0) / 60
    speed_kmh = (activity.get("avg_speed_ms") or 0) * 3.6
    elev = activity.get("elevation_gain_m") or 0

    extra_lines = []
    if activity.get("trainer"):
        extra_lines.append("Art: Indoor-Trainer")
    if activity.get("commute"):
        extra_lines.append("Art: Pendel-Fahrt")
    achievements = activity.get("achievement_count") or 0
    if achievements:
        extra_lines.append(f"Achievements erzielt: {achievements}")
    pr_count = activity.get("pr_count") or 0
    if pr_count:
        extra_lines.append(f"Persönliche Rekorde: {pr_count}")

    extra = ("\n" + "\n".join(extra_lines)) if extra_lines else ""

    prompt = (
        f"Du bist ein erfahrener Trainer. Gib einen kurzen, motivierenden Kommentar "
        f"(1–2 Sätze, Deutsch, Du-Form) zu dieser Einheit:\n\n"
        f"Sport: {activity.get('sport_type', 'Unbekannt')}\n"
        f"Name: {activity.get('name', '')}\n"
        f"Distanz: {dist_km:.1f} km\n"
        f"Dauer: {duration_min:.0f} min\n"
        f"Ø Tempo: {speed_kmh:.1f} km/h\n"
        f"Höhenmeter: {elev:.0f} m\n"
        f"Note: {grade}{extra}\n\n"
        f"Nur der Kommentar, keine Einleitung."
    )

    try:
        response = client.messages.create(
            model=MODEL_SHORT,
            max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()
    except Exception as e:
        logger.warning("AI comment generation failed for activity %s: %s", activity.get("id"), e)
        return None


def generate_detail_comment(
    activity: dict,
    grade: str,
    client: anthropic.Anthropic,
    recent_same_sport: list[dict] | None = None,
) -> str | None:
    dist_km = (activity.get("distance_m") or 0) / 1000
    duration_min = (activity.get("moving_time_s") or 0) / 60
    speed_kmh = (activity.get("avg_speed_ms") or 0) * 3.6
    max_speed_kmh = (activity.get("max_speed_ms") or 0) * 3.6
    elev = activity.get("elevation_gain_m") or 0
    elev_high = activity.get("elev_high_m")
    elev_low = activity.get("elev_low_m")
    watts = activity.get("avg_watts")
    heartrate = activity.get("avg_heartrate")
    pr_count = activity.get("pr_count") or 0
    achievements = activity.get("achievement_count") or 0

    lines = [
        "Du bist ein erfahrener Trainer. Analysiere diese Einheit präzise (3–4 Sätze, Deutsch, Du-Form). "
        "Beziehe konkret Stellung: Was lief gut, was könnte optimiert werden, und was ist der wichtigste Trainingshinweis?\n",
        f"Sport: {activity.get('sport_type', 'Unbekannt')}",
        f"Name: {activity.get('name', '')}",
        f"Note: {grade}",
        f"Distanz: {dist_km:.1f} km",
        f"Dauer: {duration_min:.0f} min",
        f"Ø Tempo: {speed_kmh:.1f} km/h  |  Max: {max_speed_kmh:.1f} km/h",
        f"Höhenmeter: {elev:.0f} m",
    ]
    if elev_high is not None and elev_low is not None:
        lines.append(f"Höhenprofil: {elev_low:.0f}–{elev_high:.0f} m ü.NN")
    if heartrate:
        lines.append(f"Ø Herzfrequenz: {heartrate:.0f} bpm")
    if watts:
        lines.append(f"Ø Leistung: {watts:.0f} W  |  {(watts * duration_min * 60 / 1000):.0f} kJ")
    if activity.get("trainer"):
        lines.append("Art: Indoor-Trainer")
    if activity.get("commute"):
        lines.append("Art: Pendel-Fahrt")
    if pr_count:
        lines.append(f"Strava-PRs: {pr_count}")
    if achievements:
        lines.append(f"Achievements: {achievements}")

    if recent_same_sport:
        recent = recent_same_sport[-5:]
        avg_dist = sum((a.get("distance_m") or 0) / 1000 for a in recent) / len(recent)
        avg_spd = sum((a.get("avg_speed_ms") or 0) * 3.6 for a in recent) / len(recent)
        lines.append(
            f"\nVergleich letzte {len(recent)} Einheiten (selber Sport): "
            f"Ø {avg_dist:.1f} km, Ø {avg_spd:.1f} km/h"
        )

    lines.append("\nNur die Analyse, keine Einleitung.")
    prompt = "\n".join(lines)

    try:
        response = client.messages.create(
            model=MODEL_DETAIL,
            max_tokens=MAX_TOKENS_DETAIL,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()
    except Exception as e:
        logger.warning("Detail comment generation failed for activity %s: %s", activity.get("id"), e)
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
        activity = _enrich_from_raw(dict(row))
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
