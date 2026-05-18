# Strava Dashboard

A local performance dashboard for Strava activities with AI-powered coaching insights.

Built on top of [JonoCX/strava-mcp](https://github.com/JonoCX/strava-mcp) with a full dashboard layer added on top.

## Features

- **Sync** — fetches your Strava activities into a local SQLite cache with token refresh
- **Grading** — grades every activity A+→C using a composite percentile score (distance 50%, EAS speed 30%, elevation 20%)
- **AI coach comments** — one-sentence coaching comment per activity via Claude Haiku, generated after each sync
- **Performance dashboard** — dark-theme FastAPI web app at `localhost:8080`:
  - Weekly summary cards (distance, EAS speed, elevation) with sparklines and week-over-week comparisons
  - Activity feed with grade badges and AI comments
  - Trend charts (EAS speed & volume) over 4/12/26/52 weeks
  - Sport and time-window filters
- **Activity detail panel** — click any activity to slide open a detail view with:
  - Full stats (max speed, elevation profile, watts, heart rate, PRs, energy)
  - Route map via Leaflet + OpenStreetMap
  - 3–4 sentence AI coaching analysis from Claude Haiku

## Setup

### Dependencies

- `uv`
- A Strava account with a developer app — [create one here](https://www.strava.com/settings/api)
- An Anthropic API key (for AI comments) — [get one here](https://console.anthropic.com/)

### 1. Install dependencies

```bash
uv sync
```

### 2. Configure credentials

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

```bash
STRAVA_CLIENT_ID=<your_client_id>
STRAVA_CLIENT_SECRET=<your_client_secret>
STRAVA_ACCESS_TOKEN=<your_access_token>
STRAVA_REFRESH_TOKEN=<your_refresh_token>
ANTHROPIC_API_KEY=<your_anthropic_api_key>
```

To get your Strava tokens, follow the [Strava OAuth guide](https://developers.strava.com/docs/getting-started/).

### 3. Sync activities

```bash
uv run sync.py
```

This fetches your activities, stores them locally, and generates AI coaching comments for recent rides.

### 4. Open the dashboard

```bash
uv run dashboard.py
```

Opens `http://localhost:8080` in your browser.

## MCP server (optional)

The original MCP server for Claude Desktop is still included:

```bash
uv run mcp install main.py
```

## Running tests

```bash
uv run pytest
```

## Tech stack

Python 3.12 · FastAPI · SQLite · anthropic SDK · Tailwind CSS · Leaflet.js · pytest
