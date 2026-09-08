"""Live MCP stdio transport — exercises apatch-mcp as Cursor/Claude would."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp", reason="mcp extra not installed")

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER_CMD = StdioServerParameters(
    command=sys.executable,
    args=["-m", "apatch.mcp.launcher"],
    env={**os.environ, "APATCH_MCP_PROFILE": "full"},
)


def _tool_payload(result) -> dict | list:
    assert not result.isError, getattr(result, "content", result)
    assert result.content, "tool returned empty content"
    return json.loads(result.content[0].text)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.asyncio
async def test_mcp_stdio_lists_all_tools():
    async with stdio_client(SERVER_CMD) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = {t.name for t in tools.tools}
            expected = {
                "apatch_plan",
                "apatch_plan_batch",
                "apatch_apply",
                "apatch_scan",
                "apatch_view",
                "apatch_strip_dry_run",
                "apatch_strip",
                "apatch_phase_run",
                "apatch_rollback",
                "apatch_generate",
                "apatch_doctor",
                "apatch_init_consumer",
                "apatch_natives_check",
                "apatch_compile",
                "apatch_suggest_until",
                "apatch_arch_check",
                "apatch_impact",
                "apatch_db_check",
                "apatch_db_revision",
                "apatch_db_safety",
                "apatch_db_run",
                "apatch_refactor_run",
                "apatch_verify_semantic",
                "apatch_index_build",
                "apatch_index_query",
                "apatch_trustchain_history",
                "apatch_pipeline_run",
            }
            assert expected.issubset(names)


@pytest.mark.asyncio
async def test_mcp_stdio_apatch_plan_roundtrip(tmp_path):
    f = tmp_path / "sample.py"
    f.write_text("value = 1\n", encoding="utf-8")

    async with stdio_client(SERVER_CMD) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "apatch_plan",
                {
                    "target_file": "sample.py",
                    "old_content": "value = 1",
                    "new_content": "value = 2",
                    "target_dir": str(tmp_path),
                },
            )
            payload = _tool_payload(result)
            assert payload["success"] is True
            assert payload["strategy"] == "exact"


@pytest.mark.asyncio
async def test_mcp_stdio_apatch_doctor_roundtrip(tmp_path):
    async with stdio_client(SERVER_CMD) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "apatch_doctor",
                {"target_dir": str(tmp_path)},
            )
            payload = _tool_payload(result)
            assert "version" in payload
            assert payload["workspace"] == str(tmp_path.resolve())
            assert "tree_sitter_grammars" in payload


@pytest.mark.asyncio
async def test_mcp_stdio_apatch_strip_dry_run_roundtrip(tmp_path):
    from tests.fixtures.planning_tsx import PLANNING_MANIFEST, PLANNING_TSX

    page = tmp_path / "src" / "Planning.tsx"
    page.parent.mkdir(parents=True)
    page.write_text(PLANNING_TSX, encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(PLANNING_MANIFEST), encoding="utf-8")

    async with stdio_client(SERVER_CMD) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "apatch_strip_dry_run",
                {
                    "file_path": "src/Planning.tsx",
                    "manifest_path": str(manifest),
                    "out_dir": "extracted",
                    "target_dir": str(tmp_path),
                },
            )
            payload = _tool_payload(result)
            assert payload["ok"] is True
            assert payload["exported_meta"]
            assert any(
                d["name"] == "handleOpenDrawer" for d in payload["dangling_references"]
            )


@pytest.mark.asyncio
async def test_mcp_stdio_apatch_generate_and_apply_roundtrip(tmp_path):
    src = tmp_path / "widget.py"
    src.write_text("LEGACY_TOKEN = 1\n", encoding="utf-8")
    out_path = tmp_path / "patches.jsonl"

    async with stdio_client(SERVER_CMD) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            gen = await session.call_tool(
                "apatch_generate",
                {
                    "find_text": "LEGACY_TOKEN",
                    "replace_text": "LEGACY_TOKEN = 2",
                    "target_dir": str(tmp_path),
                    "glob_pattern": "*.py",
                    "out_path": str(out_path),
                },
            )
            gen_payload = _tool_payload(gen)
            assert gen_payload["count"] == 1
            resolved_out = gen_payload.get("out_path") or str(out_path)
            assert Path(resolved_out).is_file()

            apply = await session.call_tool(
                "apatch_apply",
                {
                    "logs_path": resolved_out,
                    "target_dir": str(tmp_path),
                },
            )
            apply_payload = _tool_payload(apply)
            assert apply_payload["ok"] is True
            assert apply_payload["applied"] == 1
            assert "LEGACY_TOKEN = 2" in src.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_mcp_stdio_apatch_rollback_roundtrip(tmp_path):
    from apatch.backup import BackupManager

    target = tmp_path / "data.py"
    target.write_text("before\n", encoding="utf-8")
    mgr = BackupManager(str(tmp_path), session_id="stdio_rb")
    mgr.create_backup(str(target))
    target.write_text("after\n", encoding="utf-8")

    async with stdio_client(SERVER_CMD) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "apatch_rollback",
                {"target_dir": str(tmp_path), "session_id": "stdio_rb"},
            )
            payload = _tool_payload(result)
            assert payload["ok"] is True
            assert target.read_text(encoding="utf-8") == "before\n"
