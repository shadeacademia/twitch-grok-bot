"""Fast local checks. Grok only sees messages these filters mark suspicious."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

LINK = re.compile(
    r"(https?://|www\.|discord\.gg/|\b[\w-]+\.(gg|com|net|tv|ly|ru|io)/\S*)",
    re.IGNORECASE,
)
REPEAT = re.compile(r"(.)\1{9,}", re.DOTALL)
WORD = re.compile(r"[a-z0-9']+", re.IGNORECASE)


@dataclass(frozen=True)
class Verdict:
    action: str  # delete | timeout
    seconds: int
    reason: str
    ask_grok: bool = False


class LocalMod:
    def __init__(self, banned_path: Path, allow_links: bool = False):
        self.banned_path = banned_path
        self.allow_links = allow_links
        self._phrases: list[str] = []
        self._mtime: float | None = None
        self._recent: dict[str, list[tuple[float, str]]] = {}
        self.load()

    def load(self) -> None:
        phrases: list[str] = []
        if self.banned_path.exists():
            for line in self.banned_path.read_text(encoding="utf-8").splitlines():
                line = line.split("#", 1)[0].strip().lower()
                if line:
                    phrases.append(line)
            self._mtime = self.banned_path.stat().st_mtime
        else:
            self._mtime = None
        self._phrases = phrases

    def refresh(self) -> None:
        if not self.banned_path.exists():
            return
        mtime = self.banned_path.stat().st_mtime
        if mtime != self._mtime:
            self.load()

    def check(self, user: str, text: str, now: float | None = None) -> Verdict | None:
        self.refresh()
        now = time.monotonic() if now is None else now
        body = " ".join(text.split())
        lowered = body.lower()

        for phrase in self._phrases:
            if phrase in lowered:
                return Verdict("timeout", 600, "banned phrase")

        if not self.allow_links and LINK.search(body):
            return Verdict("timeout", 60, "link")

        if REPEAT.search(body):
            return Verdict("timeout", 30, "spam")

        letters = [ch for ch in body if ch.isalpha()]
        if len(letters) >= 12 and sum(ch.isupper() for ch in letters) / len(letters) >= 0.8:
            return Verdict("timeout", 30, "caps")

        stamped = self._recent.setdefault(user, [])
        stamped.append((now, lowered))
        cutoff = now - 40
        self._recent[user] = [(ts, msg) for ts, msg in stamped if ts >= cutoff]
        repeats = sum(1 for _, msg in self._recent[user] if msg == lowered)
        if lowered and repeats >= 3:
            return Verdict("timeout", 60, "repeat")

        if _looks_suspicious(body):
            return Verdict("delete", 0, "review", ask_grok=True)
        return None


def _looks_suspicious(body: str) -> bool:
    if len(body) > 350:
        return True
    symbols = sum(not ch.isalnum() and not ch.isspace() for ch in body)
    if len(body) >= 12 and symbols / len(body) >= 0.35:
        return True
    hits = ("http", "discord", "giveaway", "nitro", "follower", "crypto", "airdrop")
    lowered = body.lower()
    return any(word in lowered for word in hits)
