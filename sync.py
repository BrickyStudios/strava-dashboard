"""Sync Strava activities into local SQLite database."""

import time
import httpx
import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from dotenv import dotenv_values, set_key

from lib.db import get_conn, init_db, upsert_activity, get_sync_state, set_sync_state, upsert_segment_efforts
from lib.ai_coach import generate_missing_comments
from lib.surfaces import sync_surfaces

ENV_PATH = Path(__file__).parent / ".env"
STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"
STRAVA_ACTIVITIES_URL = "https://www.strava.com/api/v3/athlete/activities"


class DailyLimitReached(Exception):
    pass


def refresh_tokens(env_path: Path = ENV_PATH) -> str:
    envs = dotenv_values(env_path)
    resp = httpx.post(STRAVA_TOKEN_URL, data={
        "client_id": envs["STRAVA_CLIENT_ID"],
        "client_secret": envs["STRAVA_CLIENT_SECRET"],
        "refresh_token": envs["STRAVA_REFRESH_TOKEN"],
        "grant_type": "refresh_token",
    })
    resp.raise_for_status()
    data = resp.json()
    set_key(str(env_path), "STRAVA_ACCESS_TOKEN", data["access_token"])
    set_key(str(env_path), "STRAVA_REFRESH_TOKEN", data["refresh_token"])
    return data["access_token"]


def check_rate_limit(headers: dict) -> None:
    usage_raw = headers.get("X-RateLimit-Usage", "0,0")
    limit_raw = headers.get("X-RateLimit-Limit", "100,1000")
    fifteen_min, daily = (int(x) for x in usage_raw.split(","))
    _, daily_limit = (int(x) for x in limit_raw.split(","))

    if daily >= daily_limit - 10:
        raise DailyLimitReached(f"Daily limit reached: {daily}/{daily_limit}")

    if fifteen_min >= 90:
        now = datetime.now()
        current_quarter = (now.minute // 15)
        next_quarter_minute = (current_quarter + 1) * 15
        if next_quarter_minute >= 60:
            sleep_s = (60 - now.minute) * 60 - now.second + 5
        else:
            sleep_s = (next_quarter_minute - now.minute) * 60 - now.second + 5
        print(f"Rate limit at {fifteen_min}/100 — sleeping {sleep_s}s until next window...")
        time.sleep(sleep_s)


def run_sync(conn: sqlite3.Connection, full: bool, last_epoch: int, access_token: str = "fake") -> int:
    after = 0 if full else (last_epoch or 0)
    page = 1
    total = 0

    while True:
        resp = httpx.get(
            STRAVA_ACTIVITIES_URL,
            params={"per_page": 200, "page": page, "after": after},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        check_rate_limit(resp.headers)

        activities = resp.json()
        if not activities:
            break

        for activity in activities:
            upsert_activity(conn, activity)

        total += len(activities)
        print(f"Page {page}: synced {len(activities)} activities ({total} total)")
        page += 1

    return total


def parse_segment_effort(effort: dict) -> dict:
    seg = effort.get("segment") or {}
    overall_rank = None
    for a in effort.get("achievements") or []:
        if a.get("type") == "overall":
            overall_rank = a.get("rank")
            break
    return {
        "segment_id": seg.get("id"),
        "segment_name": seg.get("name"),
        "segment_distance_m": seg.get("distance"),
        "elapsed_time_s": effort.get("elapsed_time"),
        "start_date_local": effort.get("start_date_local"),
        "pr_rank": effort.get("pr_rank"),
        "overall_rank": overall_rank,
    }


def sync_segment_efforts(conn: sqlite3.Connection, access_token: str) -> None:
    rows = conn.execute("""
        SELECT id FROM activities
        WHERE id NOT IN (SELECT DISTINCT activity_id FROM segment_efforts)
        ORDER BY start_date_local DESC
    """).fetchall()

    if not rows:
        print("All activities already have segment data.")
        return

    print(f"Fetching segment efforts for {len(rows)} activities...")
    for row in rows:
        activity_id = row[0]
        resp = httpx.get(
            f"https://www.strava.com/api/v3/activities/{activity_id}?include_all_efforts=true",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if resp.status_code == 404:
            print(f"  Activity {activity_id}: not found on Strava, skipping")
            upsert_segment_efforts(conn, activity_id, [])
            continue
        resp.raise_for_status()
        check_rate_limit(resp.headers)
        detail = resp.json()
        raw_efforts = detail.get("segment_efforts") or []
        parsed = [parse_segment_effort(e) for e in raw_efforts]
        upsert_segment_efforts(conn, activity_id, parsed)
        print(f"  Activity {activity_id}: {len(parsed)} segment efforts stored")


def main():
    parser = argparse.ArgumentParser(description="Sync Strava activities to SQLite")
    parser.add_argument("--full", action="store_true", help="Re-fetch all activities")
    args = parser.parse_args()

    conn = get_conn()
    init_db(conn)

    print("Refreshing Strava tokens...")
    access_token = refresh_tokens()

    state = get_sync_state(conn)
    last_epoch = 0 if args.full else (state["last_synced_epoch"] if state else 0)

    if args.full:
        print("Full sync — fetching all activities...")
    else:
        print(f"Incremental sync from epoch {last_epoch}...")

    try:
        synced = run_sync(conn, full=args.full, last_epoch=last_epoch, access_token=access_token)
    except DailyLimitReached as e:
        print(f"Warning: {e}. Run again tomorrow to continue.")
        return

    total_count = conn.execute("SELECT COUNT(*) FROM activities").fetchone()[0]
    set_sync_state(conn, epoch=int(datetime.now(timezone.utc).timestamp()), count=total_count)
    print(f"Sync complete. {synced} new activities. {total_count} total in DB.")

    print("Generating AI coaching comments for new activities...")
    generate_missing_comments(conn)

    print("Syncing segment efforts...")
    sync_segment_efforts(conn, access_token)

    print("Analyzing surface types via OpenStreetMap...")
    sync_surfaces(conn)
    print("Done.")


if __name__ == "__main__":
    main()
