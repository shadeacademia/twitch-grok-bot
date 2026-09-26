#!/usr/bin/env python3
"""Twitch automod + chat bot. Replies go through the local grok CLI.

That CLI uses the grok.com login on this machine (SuperGrok), not an xAI API key.
The bot only answers people on whitelist.txt, plus the channel owner.
Everyone else is moderated and otherwise ignored.
"""

from __future__ import annotations

import os
import queue
import socket
import ssl
import sys
import threading
import time
from collections import deque
from pathlib import Path

from chat import (
    CHAT_RULES,
    MOD_RULES,
    MOD_SCHEMA,
    build_chat_prompt,
    build_mod_prompt,
    chat_question,
    clean_chat_reply,
    parse_command,
    parse_mod_decision,
    strip_action,
)
from grok_bridge import GrokBridge, GrokError
from moderate import LocalMod
from whitelist import Whitelist, normalize_login

ROOT = Path(__file__).resolve().parent
HOST = "irc.chat.twitch.tv"
PORT = 6697


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        os.environ.setdefault(key, value)


def env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def parse_irc(line: str) -> tuple[dict[str, str], str | None, str, list[str], str | None]:
    tags: dict[str, str] = {}
    if line.startswith("@"):
        raw_tags, line = line.split(" ", 1)
        for part in raw_tags[1:].split(";"):
            if not part:
                continue
            key, _, value = part.partition("=")
            tags[key] = value.replace("\\s", " ").replace("\\:", ";")
    prefix = None
    if line.startswith(":"):
        prefix, _, line = line.partition(" ")
        prefix = prefix[1:]
    trailing = None
    if " :" in line:
        line, _, trailing = line.partition(" :")
    parts = line.split()
    command = parts[0] if parts else ""
    return tags, prefix, command, parts[1:], trailing


def login_from_prefix(prefix: str | None) -> str:
    if not prefix:
        return ""
    return prefix.split("!", 1)[0].lower()


def is_privileged(tags: dict[str, str], login: str, owners: set[str]) -> bool:
    if login in owners:
        return True
    badges = tags.get("badges", "")
    return "broadcaster/" in badges or "moderator/" in badges or tags.get("mod") == "1"


class Bot:
    def __init__(self) -> None:
        self.nick = os.environ["TWITCH_NICK"].strip().lstrip("#").lower()
        oauth = os.environ["TWITCH_OAUTH"].strip()
        if oauth and not oauth.lower().startswith("oauth:"):
            oauth = "oauth:" + oauth
        self.oauth = oauth
        self.channel = os.environ["TWITCH_CHANNEL"].strip().lstrip("#").lower()
        owners = os.environ.get("TWITCH_OWNERS", self.channel)
        self.owners = {login for name in owners.split(",") if (login := normalize_login(name))}
        self.whitelist = Whitelist(ROOT / os.environ.get("WHITELIST_PATH", "whitelist.txt"))
        self.mod = LocalMod(
            ROOT / os.environ.get("BANNED_PATH", "banned.txt"),
            allow_links=env_flag("ALLOW_LINKS", False),
        )
        self.reply_all = env_flag("REPLY_ALL_WHITELIST", False)
        self.grok_automod = os.environ.get("GROK_AUTOMOD", "suspect").strip().lower()
        self.cooldown = float(os.environ.get("CHAT_COOLDOWN", "15"))
        sandbox = ROOT / "var" / "sandbox"
        self.grok = GrokBridge(
            binary=os.environ.get("GROK_BIN", "grok"),
            model=os.environ.get("GROK_MODEL", "grok-4.7-build-fast"),
            effort=os.environ.get("GROK_EFFORT", "low"),
            cwd=sandbox,
            leader_socket=ROOT / "var" / "leader.sock",
            timeout=float(os.environ.get("GROK_TIMEOUT", "75")),
            sandbox=os.environ.get("GROK_SANDBOX", "strict"),
        )
        self.recent: deque[tuple[str, str]] = deque(maxlen=10)
        self.recent_lock = threading.Lock()
        self.last_reply: dict[str, float] = {}
        self.jobs: queue.Queue[tuple[str, str, str, str]] = queue.Queue(maxsize=8)
        self.outbound: queue.Queue[str] = queue.Queue()
        self.sock: socket.socket | None = None
        self.stop = threading.Event()

    def may_chat(self, login: str) -> bool:
        return login in self.owners or self.whitelist.allows(login)

    def run(self) -> None:
        worker = threading.Thread(target=self._worker, name="grok", daemon=True)
        worker.start()
        while not self.stop.is_set():
            try:
                self._session()
            except (OSError, ssl.SSLError) as exc:
                if self.stop.is_set():
                    break
                print(f"connection dropped: {exc}. retrying in 5s", flush=True)
                time.sleep(5)

    def _session(self) -> None:
        raw = socket.create_connection((HOST, PORT), timeout=30)
        wrapped = ssl.create_default_context().wrap_socket(raw, server_hostname=HOST)
        wrapped.settimeout(1)
        self.sock = wrapped
        self._send(f"PASS {self.oauth}")
        self._send(f"NICK {self.nick}")
        self._send("CAP REQ :twitch.tv/tags twitch.tv/commands")
        buffer = ""
        joined = False
        last_ping = time.monotonic()
        try:
            while not self.stop.is_set():
                if time.monotonic() - last_ping > 240:
                    self._send("PING :tmi.twitch.tv")
                    last_ping = time.monotonic()
                self._flush()
                try:
                    chunk = wrapped.recv(8192)
                except socket.timeout:
                    continue
                if not chunk:
                    raise OSError("twitch closed the connection")
                buffer += chunk.decode("utf-8", errors="replace")
                while "\r\n" in buffer:
                    line, buffer = buffer.split("\r\n", 1)
                    if not line:
                        continue
                    joined = self._on_line(line, joined) or joined
        finally:
            sock = self.sock
            self.sock = None
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass

    def _on_line(self, line: str, joined: bool) -> bool:
        tags, prefix, command, args, trailing = parse_irc(line)
        if command == "PING":
            self._send("PONG :" + (trailing or "tmi.twitch.tv"))
            return joined
        if command == "001" and not joined:
            self._send(f"JOIN #{self.channel}")
            print(f"joined #{self.channel} as {self.nick}", flush=True)
            print(
                f"chat whitelist: {', '.join(self.whitelist.names()) or '(nobody yet)'}"
                f" plus owner {', '.join(sorted(self.owners))}",
                flush=True,
            )
            return True
        if command == "NOTICE" and trailing and "Login authentication failed" in trailing:
            raise SystemExit("Twitch rejected the oauth token. Check TWITCH_OAUTH.")
        if command != "PRIVMSG" or not trailing:
            return joined
        if args and args[0].lstrip("#").lower() != self.channel:
            return joined
        login = login_from_prefix(prefix)
        if not login or login == self.nick:
            return joined
        self._on_message(login, tags, strip_action(trailing))
        return joined

    def _on_message(self, login: str, tags: dict[str, str], text: str) -> None:
        privileged = is_privileged(tags, login, self.owners)
        command = parse_command(text)
        if command and command[0] in {"allow", "unallow", "whitelist"}:
            self._owner_command(login, command[0], command[1])
            return

        if not privileged:
            self._local_moderate(login, text, tags.get("id", ""))

        with self.recent_lock:
            self.recent.append((login, text))
        if not self.may_chat(login):
            return
        question = chat_question(text, self.nick, self.reply_all)
        if not question:
            return
        now = time.monotonic()
        if now - self.last_reply.get(login, 0) < self.cooldown:
            return
        if self._enqueue("chat", login, question, ""):
            self.last_reply[login] = now

    def _owner_command(self, login: str, name: str, arg: str) -> None:
        if login not in self.owners:
            if self.may_chat(login):
                self.say("only the channel owner can change who I talk to")
            return
        if name == "whitelist":
            names = self.whitelist.names()
            shown = ", ".join(names[:30]) if names else "nobody yet"
            extra = f" (+{len(names) - 30} more)" if len(names) > 30 else ""
            self.say(f"whitelist: {shown}{extra}")
            return
        target = arg.split()[0] if arg else ""
        if name == "allow":
            added = self.whitelist.add(target)
            self.say(f"added {added}" if added else "use !allow username")
            return
        removed = self.whitelist.remove(target)
        self.say(f"removed {removed}" if removed else "use !unallow username")

    def _local_moderate(self, login: str, text: str, msg_id: str) -> None:
        verdict = self.mod.check(login, text)
        if verdict is None:
            if self.grok_automod == "all":
                self._enqueue("mod", login, text, msg_id)
            return
        if verdict.ask_grok:
            if self.grok_automod in {"suspect", "all"}:
                self._enqueue("mod", login, text, msg_id)
            return
        if verdict.action == "delete" and msg_id:
            self._send(f"PRIVMSG #{self.channel} :/delete {msg_id}")
            print(f"delete {login}: {verdict.reason}", flush=True)
            return
        if verdict.action == "timeout":
            self._send(
                f"PRIVMSG #{self.channel} :/timeout {login} {verdict.seconds} {verdict.reason}"
            )
            print(f"timeout {login} {verdict.seconds}s: {verdict.reason}", flush=True)

    def _enqueue(self, kind: str, user: str, text: str, msg_id: str) -> bool:
        try:
            self.jobs.put_nowait((kind, user, text, msg_id))
        except queue.Full:
            if kind == "chat":
                self.say(f"@{user} busy, try again in a minute")
            return False
        return True

    def _worker(self) -> None:
        while not self.stop.is_set():
            try:
                kind, user, text, msg_id = self.jobs.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                if kind == "chat":
                    self._answer(user, text)
                else:
                    self._review(user, text, msg_id)
            except GrokError as exc:
                print(f"grok {kind} failed: {exc}", flush=True)
                if kind == "chat":
                    self.say(f"@{user} I couldn't get an answer out just then")
            except Exception as exc:
                print(f"worker error: {exc}", flush=True)

    def _answer(self, user: str, question: str) -> None:
        started = time.monotonic()
        with self.recent_lock:
            recent = list(self.recent)
        prompt = build_chat_prompt(user, question, recent)
        text = clean_chat_reply(self.grok.ask(prompt, CHAT_RULES))
        if not text:
            return
        if len(text) > 450:
            text = text[:447].rstrip() + "..."
        self.say(f"@{user} {text}")
        print(f"reply to {user} in {time.monotonic() - started:.1f}s", flush=True)

    def _review(self, user: str, text: str, msg_id: str) -> None:
        raw = self.grok.ask(build_mod_prompt(user, text), MOD_RULES, schema=MOD_SCHEMA)
        decision = parse_mod_decision(raw)
        if not decision or decision[0] == "allow":
            return
        action, seconds, reason = decision
        if action == "delete" and msg_id:
            self._send(f"PRIVMSG #{self.channel} :/delete {msg_id}")
        elif action == "timeout":
            self._send(f"PRIVMSG #{self.channel} :/timeout {user} {seconds} {reason}")
        print(f"grok mod {action} {user}: {reason}", flush=True)

    def say(self, text: str) -> None:
        text = " ".join(text.split())
        if not text:
            return
        self._send(f"PRIVMSG #{self.channel} :{text[:480]}")

    def _send(self, line: str) -> None:
        self.outbound.put(line)

    def _flush(self) -> None:
        sock = self.sock
        if sock is None:
            return
        while True:
            try:
                line = self.outbound.get_nowait()
            except queue.Empty:
                return
            try:
                sock.sendall((line + "\r\n").encode("utf-8"))
            except OSError as exc:
                print(f"send failed: {exc}", flush=True)
                return


def main() -> None:
    load_env(ROOT / ".env")
    missing = [key for key in ("TWITCH_NICK", "TWITCH_OAUTH", "TWITCH_CHANNEL") if not os.environ.get(key)]
    if missing:
        print("Missing " + ", ".join(missing) + ".", file=sys.stderr)
        print(f"Copy {ROOT / '.env.example'} to {ROOT / '.env'} and fill it in.", file=sys.stderr)
        print("Get a chat token while logged in as the bot account:", file=sys.stderr)
        print("  https://twitchapps.com/tmi/", file=sys.stderr)
        print("Then mod that account in your channel so timeouts work.", file=sys.stderr)
        print("Add chatters with !allow username, from the channel owner account.", file=sys.stderr)
        raise SystemExit(2)
    print("chat goes through the grok login on this machine, not an API key", flush=True)
    Bot().run()


if __name__ == "__main__":
    main()
