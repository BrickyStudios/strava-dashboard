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
MODEL_SHORT = "claude-haiku-5-5"    # one sentence per activity, runs for every new activity
MODEL_DETAIL = "claude-sonnet-5-5"  # 3–4 sentence analysis, only when a detail panel opens
# Thinking counts against max_tokens, so leave headroom above the visible answer.
MAX_TOKENS = 1024
MAX_TOKENS_DETAIL = 4000
# Sonnet 5.5: on a policy decline the API retries on a fallback model in the same call.
FALLBACK_HEADERS = {"anthropic-beta": "server-side-fallback-2026-07-01"}
FALLBACK_BODY = {"fallbacks": "default"}

logger = logging.getLogger(__name__)


def _get_api_key() -> str:
    return dotenv_values(ENV_PATH).get("ANTHROPIC_API_KEY", "")


def _language() -> str:
    """Language of the coach's comments, e.g. COACH_LANGUAGE=German in .env. Default English."""
    return dotenv_values(ENV_PATH).get("COACH_LANGUAGE") or "English"


def _text(response) -> str | None:
    """The answer text; None on a refusal. Thinking blocks come first, so don't read content[0]."""
    if response.stop_reason == "refusal":
        return None
    text = "".join(b.text for b in response.content if b.type == "text").strip()
    return text or None


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
        extra_lines.append("Type: indoor trainer")
    if activity.get("commute"):
        extra_lines.append("Type: commute")
    achievements = activity.get("achievement_count") or 0
    if achievements:
        extra_lines.append(f"Achievements: {achievements}")
    pr_count = activity.get("pr_count") or 0
    if pr_count:
        extra_lines.append(f"Personal records: {pr_count}")

    extra = ("\n" + "\n".join(extra_lines)) if extra_lines else ""

    prompt = (
        f"You are an experienced endurance coach. Write a short, motivating comment "
        f"(1–2 sentences, in {_language()}, addressing the athlete directly) on this session:\n\n"
        f"Sport: {activity.get('sport_type', 'unknown')}\n"
        f"Name: {activity.get('name', '')}\n"
        f"Distance: {dist_km:.1f} km\n"
        f"Duration: {duration_min:.0f} min\n"
        f"Avg speed: {speed_kmh:.1f} km/h\n"
        f"Elevation gain: {elev:.0f} m\n"
        f"Grade: {grade}{extra}\n\n"
        f"Reply with the comment only."
    )

    try:
        response = client.messages.create(
            model=MODEL_SHORT,
            max_tokens=MAX_TOKENS,
            thinking={"type": "disabled"},  # a one-liner needs no reasoning; allowed on Haiku 5.5 up to effort high
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": prompt}],
        )
        return _text(response)
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
        f"You are an experienced endurance coach. Analyse this session precisely (3–4 sentences, in {_language()}, "
        "addressing the athlete directly). Be specific: what went well, what could be better, "
        "and the single most important training tip?\n",
        f"Sport: {activity.get('sport_type', 'unknown')}",
        f"Name: {activity.get('name', '')}",
        f"Grade: {grade}",
        f"Distance: {dist_km:.1f} km",
        f"Duration: {duration_min:.0f} min",
        f"Avg speed: {speed_kmh:.1f} km/h  |  Max: {max_speed_kmh:.1f} km/h",
        f"Elevation gain: {elev:.0f} m",
    ]
    if elev_high is not None and elev_low is not None:
        lines.append(f"Altitude range: {elev_low:.0f}–{elev_high:.0f} m")
    if heartrate:
        lines.append(f"Avg heart rate: {heartrate:.0f} bpm")
    if watts:
        lines.append(f"Avg power: {watts:.0f} W  |  {(watts * duration_min * 60 / 1000):.0f} kJ")
    if activity.get("trainer"):
        lines.append("Type: indoor trainer")
    if activity.get("commute"):
        lines.append("Type: commute")
    if pr_count:
        lines.append(f"Strava PRs: {pr_count}")
    if achievements:
        lines.append(f"Achievements: {achievements}")

    if recent_same_sport:
        recent = recent_same_sport[-5:]
        avg_dist = sum((a.get("distance_m") or 0) / 1000 for a in recent) / len(recent)
        avg_spd = sum((a.get("avg_speed_ms") or 0) * 3.6 for a in recent) / len(recent)
        lines.append(
            f"\nLast {len(recent)} sessions of the same sport: "
            f"avg {avg_dist:.1f} km, avg {avg_spd:.1f} km/h"
        )

    lines.append("\nReply with the analysis only.")
    prompt = "\n".join(lines)

    try:
        response = client.messages.create(
            model=MODEL_DETAIL,
            max_tokens=MAX_TOKENS_DETAIL,
            output_config={"effort": "low"},  # short analysis; thinking stays on (it can't be disabled on Sonnet 5.5)
            messages=[{"role": "user", "content": prompt}],
            extra_headers=FALLBACK_HEADERS,
            extra_body=FALLBACK_BODY,
        )
        return _text(response)
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
