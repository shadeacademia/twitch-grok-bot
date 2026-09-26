"""Who the bot is allowed to talk to."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

LOGIN = re.compile(r"^[a-z0-9_]{3,25}$")


def normalize_login(name: str) -> str | None:
    cleaned = name.strip().lstrip("@").lower()
    if not LOGIN.fullmatch(cleaned):
        return None
    return cleaned


class Whitelist:
    def __init__(self, path: Path):
        self.path = path
        self._names: set[str] = set()
        self._mtime: float | None = None
        self.load()

    def load(self) -> None:
        names: set[str] = set()
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.split("#", 1)[0].strip()
                if not line:
                    continue
                login = normalize_login(line)
                if login:
                    names.add(login)
            self._mtime = self.path.stat().st_mtime
        else:
            self._mtime = None
        self._names = names

    def refresh(self) -> None:
        if not self.path.exists():
            if self._names:
                self._names = set()
            self._mtime = None
            return
        mtime = self.path.stat().st_mtime
        if self._mtime != mtime:
            self.load()

    def allows(self, login: str) -> bool:
        self.refresh()
        normalized = normalize_login(login)
        return normalized is not None and normalized in self._names

    def names(self) -> list[str]:
        self.refresh()
        return sorted(self._names)

    def add(self, login: str) -> str | None:
        normalized = normalize_login(login)
        if normalized is None:
            return None
        self.refresh()
        self._names.add(normalized)
        self._write()
        return normalized

    def remove(self, login: str) -> str | None:
        normalized = normalize_login(login)
        if normalized is None:
            return None
        self.refresh()
        self._names.discard(normalized)
        self._write()
        return normalized

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# One Twitch login per line. Owner commands: !allow name / !unallow name",
            *sorted(self._names),
            "",
        ]
        data = "\n".join(lines)
        fd, tmp_name = tempfile.mkstemp(dir=str(self.path.parent), prefix=".whitelist.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(data)
            os.replace(tmp_name, self.path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        self._mtime = self.path.stat().st_mtime
