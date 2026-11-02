"""Connect your Strava account: opens the Strava consent page and writes the tokens into .env.

    uv run auth.py

Needs STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET from https://www.strava.com/settings/api,
with "Authorization Callback Domain" set to localhost.
"""

import http.server
import secrets
import urllib.parse
import webbrowser
from pathlib import Path

import httpx
from dotenv import dotenv_values, set_key

ENV_PATH = Path(__file__).parent / ".env"
PORT = 8765
REDIRECT_URI = f"http://localhost:{PORT}/callback"
AUTHORIZE_URL = "https://www.strava.com/oauth/authorize"
TOKEN_URL = "https://www.strava.com/oauth/token"
SCOPE = "read,activity:read_all"  # read_all includes private activities


def authorize_url(client_id: str, state: str) -> str:
    return AUTHORIZE_URL + "?" + urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": REDIRECT_URI, "response_type": "code",
        "approval_prompt": "auto", "scope": SCOPE, "state": state,
    })


def exchange_code(client_id: str, client_secret: str, code: str) -> dict:
    resp = httpx.post(TOKEN_URL, data={
        "client_id": client_id, "client_secret": client_secret,
        "code": code, "grant_type": "authorization_code",
    })
    resp.raise_for_status()
    return resp.json()


def wait_for_code(state: str) -> str:
    """Serves one request on localhost and returns the ?code= Strava redirects back with."""
    result: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            ok = q.get("state", [""])[0] == state and "code" in q
            if ok:
                result["code"] = q["code"][0]
                result["scope"] = q.get("scope", [""])[0]
            else:
                result["error"] = q.get("error", ["state mismatch"])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            msg = "Connected. You can close this tab." if ok else f"Strava login failed: {result['error']}"
            self.wfile.write(f"<p style='font-family:sans-serif'>{msg}</p>".encode())

        def log_message(self, *args):
            pass

    with http.server.HTTPServer(("127.0.0.1", PORT), Handler) as server:
        server.handle_request()
    if "error" in result:
        raise SystemExit(f"Strava login failed: {result['error']}")
    if "activity:read" not in result.get("scope", ""):
        raise SystemExit("Strava didn't grant access to your activities. Run auth.py again and tick that box.")
    return result["code"]


def main() -> None:
    ENV_PATH.touch(exist_ok=True)
    envs = dotenv_values(ENV_PATH)
    client_id = envs.get("STRAVA_CLIENT_ID") or input("Strava client ID: ").strip()
    client_secret = envs.get("STRAVA_CLIENT_SECRET") or input("Strava client secret: ").strip()
    set_key(str(ENV_PATH), "STRAVA_CLIENT_ID", client_id)
    set_key(str(ENV_PATH), "STRAVA_CLIENT_SECRET", client_secret)

    state = secrets.token_urlsafe(16)  # proves the redirect comes from the login we started
    url = authorize_url(client_id, state)
    print("Opening Strava in your browser. If nothing opens, visit:\n" + url)
    webbrowser.open(url)
    tokens = exchange_code(client_id, client_secret, wait_for_code(state))
    set_key(str(ENV_PATH), "STRAVA_ACCESS_TOKEN", tokens["access_token"])
    set_key(str(ENV_PATH), "STRAVA_REFRESH_TOKEN", tokens["refresh_token"])
    name = (tokens.get("athlete") or {}).get("firstname", "")
    print(f"Connected{' as ' + name if name else ''}. Tokens saved to {ENV_PATH.name}. Next: uv run sync.py")


if __name__ == "__main__":
    main()
