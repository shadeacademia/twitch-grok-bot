"""One-shot questions through the local grok CLI (grok.com login), not the metered API."""

from __future__ import annotations

import json
import os
import signal
import subprocess
from pathlib import Path

DISALLOWED = ",".join(
    [
        "read_file",
        "search_replace",
        "grep",
        "list_dir",
        "run_terminal_cmd",
        "run_terminal_command",
        "web_search",
        "web_fetch",
        "todo_write",
        "spawn_subagent",
        "memory_search",
        "memory_get",
        "Agent",
        "monitor",
        "search_tool",
        "use_tool",
        "workflow",
        "enter_plan_mode",
        "exit_plan_mode",
        "ask_user_question",
        "send_feedback",
        "image_gen",
        "image_edit",
        "image_to_video",
        "reference_to_video",
        "write",
    ]
)

DENY_RULES = ("*", "Bash", "Read", "Edit", "Write", "Grep", "WebFetch", "WebSearch", "MCPTool")


class GrokError(RuntimeError):
    pass


def parse_grok_json(stdout: str) -> dict:
    start = stdout.find("{")
    end = stdout.rfind("}")
    if start < 0 or end <= start:
        raise GrokError("grok returned no json")
    try:
        data = json.loads(stdout[start : end + 1])
    except json.JSONDecodeError as exc:
        raise GrokError("grok returned unreadable json") from exc
    if not isinstance(data, dict):
        raise GrokError("grok json was not an object")
    return data


def reply_text(stdout: str) -> str:
    text = str(parse_grok_json(stdout).get("text") or "").strip()
    if text.startswith("```") and text.endswith("```"):
        text = text.strip("`").strip()
    return " ".join(text.split())


class GrokBridge:
    def __init__(
        self,
        binary: str,
        model: str,
        effort: str,
        cwd: Path,
        leader_socket: Path,
        timeout: float = 75,
        sandbox: str = "strict",
    ):
        self.binary = binary
        self.model = model
        self.effort = effort
        self.cwd = cwd
        self.leader_socket = leader_socket
        self.timeout = timeout
        self.sandbox = sandbox
        self.cwd.mkdir(parents=True, exist_ok=True)
        self.leader_socket.parent.mkdir(parents=True, exist_ok=True)

    def ask(self, prompt: str, rules: str, schema: str | None = None) -> str:
        cmd = [
            self.binary,
            "-p",
            prompt,
            "--verbatim",
            "--model",
            self.model,
            "--effort",
            self.effort,
            "--max-turns",
            "1",
            "--no-subagents",
            "--disable-web-search",
            "--permission-mode",
            "dontAsk",
            "--disallowed-tools",
            DISALLOWED,
            "--output-format",
            "json",
            "--cwd",
            str(self.cwd),
            "--leader-socket",
            str(self.leader_socket),
            "--rules",
            rules,
            "--no-auto-update",
        ]
        if self.sandbox and self.sandbox != "off":
            cmd.extend(["--sandbox", self.sandbox])
        for rule in DENY_RULES:
            cmd.extend(["--deny", rule])
        if schema:
            cmd.extend(["--json-schema", schema])

        env = os.environ.copy()
        env.pop("XAI_API_KEY", None)
        env["GROK_MANAGED_MCPS_ENABLED"] = "0"
        env["GROK_CURSOR_MCPS_ENABLED"] = "0"
        env["GROK_CLAUDE_MCPS_ENABLED"] = "0"
        env["GROK_MANAGED_MCP_GATEWAY_TOOLS_ENABLED"] = "0"

        proc = subprocess.Popen(
            cmd,
            cwd=str(self.cwd),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=self.timeout)
        except subprocess.TimeoutExpired as exc:
            _kill_group(proc.pid)
            proc.communicate()
            raise GrokError(f"grok timed out after {self.timeout:.0f}s") from exc
        if proc.returncode != 0:
            tail = (stderr or stdout or "").strip().splitlines()
            detail = tail[-1][:240] if tail else f"exit {proc.returncode}"
            raise GrokError(detail)
        return reply_text(stdout or "")


def _kill_group(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
