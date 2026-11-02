"""OAuth helper: the consent URL and the local callback, without touching Strava."""
import threading
import urllib.parse
import urllib.request
from unittest.mock import MagicMock, patch

import pytest

import auth


def test_authorize_url_asks_for_activities():
    q = urllib.parse.parse_qs(urllib.parse.urlparse(auth.authorize_url("123", "s1")).query)
    assert q["client_id"] == ["123"] and q["state"] == ["s1"]
    assert "activity:read_all" in q["scope"][0]
    assert q["redirect_uri"] == [auth.REDIRECT_URI]


def _hit(path):
    def go():
        for _ in range(50):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{auth.PORT}{path}", timeout=1).read()
                return
            except OSError:
                threading.Event().wait(0.05)
    threading.Thread(target=go, daemon=True).start()


def test_callback_returns_code():
    _hit("/callback?state=good&code=abc&scope=read,activity:read_all")
    assert auth.wait_for_code("good") == "abc"


def test_callback_rejects_wrong_state():
    _hit("/callback?state=evil&code=abc&scope=read,activity:read_all")
    with pytest.raises(SystemExit):
        auth.wait_for_code("good")


def test_callback_rejects_missing_activity_scope():
    _hit("/callback?state=good&code=abc&scope=read")
    with pytest.raises(SystemExit):
        auth.wait_for_code("good")


def test_exchange_code_posts_authorization_code():
    resp = MagicMock()
    resp.json.return_value = {"access_token": "a", "refresh_token": "r"}
    with patch("auth.httpx.post", return_value=resp) as post:
        assert auth.exchange_code("1", "s", "c")["refresh_token"] == "r"
    assert post.call_args.kwargs["data"]["grant_type"] == "authorization_code"
