"""Regression: MCP stdout must remain valid JSON-RPC under hostile locales."""

from __future__ import annotations

import json
import os
import sys

import pytest

pytest.importorskip("mcp", reason="mcp extra not installed")

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Simulate IDE hosts that omit UTF-8 env (POSIX C locale).
_HOSTILE_ENV = {
    **os.environ,
    "LC_ALL": "C",
    "LANG": "C",
}
_HOSTILE_ENV.pop("PYTHONIOENCODING", None)
_HOSTILE_ENV.pop("PYTHONUTF8", None)
_HOSTILE_ENV["APATCH_MCP_PROFILE"] = "full"

SERVER_CMD = StdioServerParameters(
    command=sys.executable,
    args=["-m", "apatch.mcp.launcher"],
    env=_HOSTILE_ENV,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _assert_stdout_json_lines(raw: bytes) -> None:
    for line in raw.splitlines():
        if not line.strip():
            continue
        json.loads(line.decode("utf-8"))


@pytest.mark.asyncio
async def test_mcp_stdio_hostile_locale_doctor(tmp_path):
    async with stdio_client(SERVER_CMD) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "apatch_doctor",
                {"target_dir": str(tmp_path)},
            )
            assert not result.isError
            payload = json.loads(result.content[0].text)
            assert payload.get("version")


@pytest.mark.asyncio
async def test_mcp_stdio_hostile_locale_apply_quiet(tmp_path):
    """Apply path uses Rich TUI — must not leak emoji to merged stdout/stderr."""
    src = tmp_path / "widget.py"
    src.write_text("TOKEN = 1\n", encoding="utf-8")
    log = tmp_path / "patches.jsonl"
    step = {
        "step_index": 1,
        "tool_calls": [
            {
                "name": "replace_file_content",
                "arguments": {
                    "TargetFile": "widget.py",
                    "TargetContent": "TOKEN = 1",
                    "ReplacementContent": "TOKEN = 2",
                },
            }
        ],
    }
    log.write_text(json.dumps(step) + "\n", encoding="utf-8")

    async with stdio_client(SERVER_CMD) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "apatch_apply",
                {
                    "logs_path": str(log),
                    "target_dir": str(tmp_path),
                    "no_trustchain": True,
                },
            )
            assert not result.isError
            payload = json.loads(result.content[0].text)
            assert payload.get("ok") is True
            assert "TOKEN = 2" in src.read_text(encoding="utf-8")
