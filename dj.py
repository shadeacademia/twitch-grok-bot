"""Song-request commands and grok-dj HTTP. No Twitch, no grok-dj process."""

from __future__ import annotations

from dataclasses import dataclass

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
