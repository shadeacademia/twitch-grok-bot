"""Song-request commands and grok-dj HTTP. No Twitch, no grok-dj process."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

HELP = (
    "grok-dj is the stream music player. !sr song requests a track (auth, when requests are on). "
    "!song is what's playing (auth). Owner: !requests on/off. Skip, pause, and resume stay on the player. "
    "Requests are {state}."
)


@dataclass(frozen=True)
class DjDecision:
    action: str  # "say" | "error" | "enqueue" | "ignore"
    channel: str = ""
    whisper: str = ""
    job: str | None = None
    query: str = ""
    requests_on: bool | None = None


def help_text(requests_on: bool) -> str:
    return HELP.format(state="on" if requests_on else "off")


def format_track(track: dict | None, fallback: str = "") -> str:
    if not track:
        return fallback
    title = str(track.get("title") or "").strip()
    artist = str(track.get("artist") or "").strip()
    if not title:
        return fallback
    if artist:
        return f"{title} — {artist}"
    return title


def error_whisper(user: str, command: str, query: str, detail: str) -> str:
    if query:
        return f"{user} !{command} {query}: {detail}"
    return f"{user} !{command}: {detail}"


def busy_replies(user: str, command: str, query: str) -> tuple[str, str]:
    return "busy, try again", error_whisper(user, command, query, "worker queue full")


def decide_dj_command(
    name: str,
    arg: str,
    *,
    user: str,
    is_owner: bool,
    may_chat: bool,
    requests_on: bool,
) -> DjDecision | None:
    if name not in {"dj", "sr", "song", "requests"}:
        return None
    if name == "dj":
        return DjDecision(action="say", channel=help_text(requests_on))
    if name == "requests":
        if is_owner:
            flag = arg.strip().lower()
            if flag == "on":
                return DjDecision(action="say", channel="requests are on", requests_on=True)
            if flag == "off":
                return DjDecision(action="say", channel="requests are off", requests_on=False)
            return DjDecision(
                action="say",
                channel="requests are on" if requests_on else "requests are off",
            )
        if may_chat:
            return DjDecision(
                action="error",
                channel="only the owner can do that",
                whisper=f"{user} tried !requests",
            )
        return DjDecision(action="ignore")
    if not may_chat:
        return DjDecision(action="ignore")
    if name == "song":
        return DjDecision(action="enqueue", job="dj_song", query="")
    query = arg.strip()
    if not query:
        return DjDecision(
            action="error",
            channel="use !sr song name",
            whisper=error_whisper(user, "sr", "", "missing song"),
        )
    if not requests_on:
        return DjDecision(
            action="error",
            channel="requests are off",
            whisper=error_whisper(user, "sr", query, "requests are off"),
        )
    return DjDecision(action="enqueue", job="dj_sr", query=query)


DEFAULT_PORT = 47721
HTTP_TIMEOUT = 20.0


@dataclass
class DjHttpResult:
    not_running: bool = False
    error: str = ""
    body: dict | None = None


def grok_dj_port(*, environ: Mapping[str, str] | None = None, home: Path | None = None) -> int:
    env = environ if environ is not None else os.environ
    xdg = env.get("XDG_CONFIG_HOME")
    if xdg:
        path = Path(xdg) / "grok-dj" / "port"
    else:
        path = Path(home if home is not None else Path.home()) / ".config" / "grok-dj" / "port"
    try:
        value = int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return DEFAULT_PORT
    if value <= 0:
        return DEFAULT_PORT
    return value


def _parse_body(raw: bytes) -> DjHttpResult:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return DjHttpResult(not_running=True)
    if not isinstance(data, dict):
        return DjHttpResult(not_running=True)
    if data.get("ok") is False:
        return DjHttpResult(error=str(data.get("error") or "Request failed"), body=data)
    return DjHttpResult(body=data)


def http_call(
    method: str,
    path: str,
    body: dict | None = None,
    *,
    port: int | None = None,
    timeout: float = HTTP_TIMEOUT,
) -> DjHttpResult:
    target = port if port is not None else grok_dj_port()
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"content-type": "application/json"} if payload is not None else {}
    req = urllib.request.Request(
        f"http://127.0.0.1:{target}{path}",
        data=payload,
        method=method,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read()
        except OSError:
            return DjHttpResult(error="Request failed")
        finally:
            exc.close()
        parsed = _parse_body(raw)
        if parsed.not_running:
            return DjHttpResult(error="Request failed")
        if parsed.error:
            return parsed
        return DjHttpResult(error=str((parsed.body or {}).get("error") or "Request failed"), body=parsed.body)
    except (urllib.error.URLError, TimeoutError, OSError):
        return DjHttpResult(not_running=True)
    return _parse_body(raw)


def run_song(user: str, *, http_call: Callable[..., DjHttpResult] = http_call) -> tuple[str, str]:
    result = http_call("GET", "/status")
    if result.not_running:
        return "DJ isn't running", error_whisper(user, "song", "", "grok-dj not running")
    if result.error:
        return "couldn't read the DJ", error_whisper(user, "song", "", result.error)
    body = result.body or {}
    now = body.get("now")
    if now:
        line = format_track(now)
        if body.get("paused"):
            return f"Paused: {line}", ""
        return f"Now: {line}", ""
    if body.get("signedIn"):
        return "Nothing playing.", ""
    return "DJ isn't signed in.", ""


def run_queue(user: str, query: str, *, http_call: Callable[..., DjHttpResult] = http_call) -> tuple[str, str]:
    before = http_call("GET", "/status")
    if before.not_running:
        return "DJ isn't running", error_whisper(user, "sr", query, "grok-dj not running")
    after = http_call("POST", "/queue", {"query": query})
    if after.not_running:
        return "DJ isn't running", error_whisper(user, "sr", query, "grok-dj not running")
    if after.error:
        return "couldn't play that", error_whisper(user, "sr", query, after.error)
    body = after.body or {}
    prior = (before.body or {}).get("now")
    if prior:
        up_next = body.get("upNext") or []
        track = up_next[0] if up_next else None
    else:
        track = body.get("now")
    line = format_track(track, fallback=query)
    return f"@{user} queued {line}", ""

