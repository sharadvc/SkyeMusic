"""ListenBrainz scrobbling (no API key; uses a personal access token).

POSTs the 'playing now' listen when a track starts and a completed scrobble
when it ends. Failures are swallowed by the caller (scrobbling is best-effort).
"""

from __future__ import annotations

import json
import urllib.request

_BASE = "https://api.listenbrainz.org/1"


def _post(token: str, payload: dict) -> None:
    req = urllib.request.Request(
        f"{_BASE}/submit-listens",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Token {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15):
        pass


def _listen(title: str, artist: str, url: str, listened_at: int | None) -> dict:
    return {
        "track_metadata": {
            "track_name": title,
            "artist_name": artist,
            "additional_info": {"recording_msid": None, "origin_url": url},
        },
        **({"listened_at": int(listened_at)} if listened_at is not None else {}),
    }


def submit_playing_now(token: str, title: str, artist: str, url: str) -> None:
    _post(token, {"listen_type": "playing_now",
                  "payload": [_listen(title, artist, url, None)]})


def submit_scrobble(token: str, title: str, artist: str, url: str,
                    listened_at: int, duration: float | None = None) -> None:
    payload = _listen(title, artist, url, listened_at)
    if duration:
        payload.setdefault("track_metadata", {})["additional_info"][
            "track_length"] = int(duration)
    _post(token, {"listen_type": "single",
                  "payload": [payload]})
