"""Strava MCP server for Claude to interact with the Strava API."""

import logging
from datetime import date, timedelta
from typing import Literal

from mcp.server.fastmcp import FastMCP

from lib.api import get_activities, get_activity, get_access_token
from lib.db import get_conn, init_db, get_activities as get_db_activities
from lib.helpers import (
    parse_activities,
    format_activity,
    filter_activities_by_date,
)

logger = logging.getLogger(__name__)
mcp = FastMCP("strava")


@mcp.tool()
async def fetch_activity(
    activity_name: str = None, on_date: str = None, sport_type: str = "run"
) -> None:
    """Fetch a specific activity from Strava.

    The activity must be in the most recent 200.

    Args:
        activity_name: the name of the activity. Defaults to None.
        on_date: the date on which the activity occurred (e.g., 2025-01-01). Defaults to None.
        sport_type: the type of activity (e.g., run or workout). Defaults to "run".

    Returns:
        the specific activity request. By default, the most recent activity is returned.
    """
    access_token = await get_access_token()

    logger.info(
        f"fetch_activity called with activity_name={activity_name}, on_date={on_date}, sport_type={sport_type}"
    )

    activities = await get_activities(access_token=access_token, n=200)

    if activity_name is None and on_date is None and sport_type == "run":
        return format_activity(activity=activities[0])

    activities = list(
        filter(lambda act: act["type"].lower() == sport_type, activities)
    )

    if on_date is not None:
        activities = filter_activities_by_date(
            activities=activities, since=on_date, until=on_date
        )

        if not activities:
            return f"Unable to find activity on the specified date. Date={on_date}"

    if activity_name is not None:
        activities = list(
            filter(lambda act: act["name"] == activity_name, activities)
        )

        if not activities:
            return f"Unable to find activity with name '{activity_name}'"

    activity = await get_activity(
        access_token=access_token, activity_id=activities[0]["id"]
    )

    return format_activity(activity=activity, is_full_activity=True)


@mcp.tool()
async def fetch_activities(
    n: int, since: str = None, until: str = None
) -> str:
    """Get a list of activities from Strava.

    Args:
        n: the number of activities to fetch (e.g., 30). Max is 200.
        since: a date to get activities from (e.g., 2025-01-01). Defaults to None.
        until: an end date to get activities to, used with the `since` parameter (e.g., 2025-01-07)

    Returns:
        a list of Strava activities.
    """
    access_token = await get_access_token()
    logger.info(
        f"fetch_activities called with n={n}, since={since}, until={until}"
    )

    if n > 200:
        return "Number of activities should be less than 200."

    activities = await get_activities(access_token=access_token, n=n)

    activities = parse_activities(activities=activities)

    logger.info(f"fetched {len(activities)} activities")

    if since is not None:
        activities = filter_activities_by_date(
            activities=activities, since=since, until=until
        )

        if until:
            logger.info(f"activities filtered between {since} and {until}")
        else:
            logger.info(f"activities filtered up to date {since}")

        logger.info(
            f"number of activities after date filter: {len(activities)}"
        )

    return "\n---\n".join(
        [format_activity(activity=activity) for activity in activities]
    )


def _iso_week_label(d: date) -> str:
    iso = d.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _week_start(d: date) -> date:
    """Monday of the ISO week containing d."""
    return d - timedelta(days=d.weekday())


def _trailing_weeks(n: int) -> list[str]:
    today = date.today()
    monday = _week_start(today)
    return [_iso_week_label(monday - timedelta(weeks=i)) for i in range(n - 1, -1, -1)]


@mcp.tool()
def get_performance_trend(
    sport_type: str | None,
    metric: Literal["avg_speed_kmh", "distance_km", "elevation_m"],
    weeks: int = 12,
) -> str:
    """Weekly performance trend from local DB.

    Args:
        sport_type: Strava sport type (e.g. 'GravelRide') or None for all.
        metric: 'avg_speed_kmh', 'distance_km', or 'elevation_m'.
        weeks: number of trailing ISO weeks to show.

    Returns:
        Formatted table of per-week aggregates.
    """
    conn = get_conn()
    init_db(conn)
    rows = get_db_activities(conn, sport_type=sport_type)

    buckets: dict[str, dict] = {}
    for row in rows:
        local_date = date.fromisoformat(row["start_date_local"][:10])
        label = _iso_week_label(local_date)
        if label not in buckets:
            buckets[label] = {"dist": 0.0, "time": 0, "elev": 0.0}
        buckets[label]["dist"] += row["distance_m"] or 0
        buckets[label]["time"] += row["moving_time_s"] or 0
        buckets[label]["elev"] += row["elevation_gain_m"] or 0

    header_map = {
        "avg_speed_kmh": "Avg Speed (km/h)",
        "distance_km": "Distance (km)",
        "elevation_m": "Elevation (m)",
    }
    lines = [f"{'Week':<12} {header_map[metric]}"]
    for label in _trailing_weeks(weeks):
        b = buckets.get(label)
        if b is None or b["time"] == 0:
            lines.append(f"{label:<12} —")
        elif metric == "avg_speed_kmh":
            val = round(b["dist"] / b["time"] * 3.6, 1)
            lines.append(f"{label:<12} {val}")
        elif metric == "distance_km":
            lines.append(f"{label:<12} {round(b['dist'] / 1000, 1)}")
        else:
            lines.append(f"{label:<12} {round(b['elev'])}")

    return "\n".join(lines)


@mcp.tool()
def get_weekly_volume(
    sport_type: str | None,
    weeks: int = 12,
) -> str:
    """Weekly distance and elevation totals from local DB.

    Args:
        sport_type: Strava sport type or None for all.
        weeks: number of trailing ISO weeks.

    Returns:
        Formatted table with km and elevation per week.
    """
    conn = get_conn()
    init_db(conn)
    rows = get_db_activities(conn, sport_type=sport_type)

    buckets: dict[str, dict] = {}
    for row in rows:
        local_date = date.fromisoformat(row["start_date_local"][:10])
        label = _iso_week_label(local_date)
        if label not in buckets:
            buckets[label] = {"dist": 0.0, "elev": 0.0}
        buckets[label]["dist"] += row["distance_m"] or 0
        buckets[label]["elev"] += row["elevation_gain_m"] or 0

    lines = [f"{'Week':<12} {'km':>8} {'Elevation (m)':>14}"]
    for label in _trailing_weeks(weeks):
        b = buckets.get(label)
        if b is None:
            lines.append(f"{label:<12} {'—':>8} {'—':>14}")
        else:
            lines.append(f"{label:<12} {round(b['dist'] / 1000, 1):>8} {round(b['elev']):>14}")

    return "\n".join(lines)


@mcp.tool()
def get_personal_bests(sport_type: str | None) -> str:
    """Personal bests from all synced activities.

    Args:
        sport_type: Strava sport type or None for all.

    Returns:
        Formatted string with longest distance, duration, fastest speed, most elevation.
    """
    conn = get_conn()
    init_db(conn)
    rows = get_db_activities(conn, sport_type=sport_type)

    if not rows:
        return "No activities found. Run: uv run sync.py"

    best_dist = max(rows, key=lambda r: r["distance_m"] or 0)
    best_dur = max(rows, key=lambda r: r["moving_time_s"] or 0)
    best_speed = max(rows, key=lambda r: r["avg_speed_ms"] or 0)
    best_elev = max(rows, key=lambda r: r["elevation_gain_m"] or 0)

    def fmt(row, extra):
        d = date.fromisoformat(row["start_date_local"][:10])
        return f"{row['name']} ({d}) — {extra}"

    dist_km = round((best_dist["distance_m"] or 0) / 1000, 1)
    dur_min = round((best_dur["moving_time_s"] or 0) / 60)
    spd_kmh = round((best_speed["avg_speed_ms"] or 0) * 3.6, 1)
    elev_m = round(best_elev["elevation_gain_m"] or 0)

    return "\n".join([
        f"longest_distance:  {fmt(best_dist, f'{dist_km} km')}",
        f"longest_duration:  {fmt(best_dur, f'{dur_min} min')}",
        f"fastest_avg_speed: {fmt(best_speed, f'{spd_kmh} km/h')}",
        f"most_elevation:    {fmt(best_elev, f'{elev_m} m')}",
    ])


if __name__ == "__main__":
    mcp.run(transport="stdio")
