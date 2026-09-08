import json
import pytest
from apatch.ingestor import LogIngestor
from apatch.strip import StripSpec, apply_strips

def test_parse_single_replace(tmp_path):
    log_file = tmp_path / "test_agent.jsonl"
    step_1 = {
        "step_index": 1,
        "tool_calls": [
            {
                "name": "replace_file_content",
                "arguments": {
                    "TargetFile": "/path/to/project/main.cpp",
                    "TargetContent": "void foo() {\n  // old\n}",
                    "ReplacementContent": "void foo() {\n  // new optimized\n}",
                    "StartLine": 10,
                    "EndLine": 12,
                },
            }
        ],
    }
    with open(log_file, "w", encoding="utf-8") as f:
        f.write(json.dumps(step_1) + "\n")

    candidates = LogIngestor(str(log_file)).parse()
    assert len(candidates) == 1
    assert candidates[0].source_format == "gemini/tool_calls"
    assert candidates[0].target_file == "/path/to/project/main.cpp"

def test_parse_replace_all_flag(tmp_path):
    log_file = tmp_path / "rename.jsonl"
    lines = [
        {
            "role": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "StrReplace",
                        "input": {
                            "path": "src/kernel.cpp",
                            "old_string": "float*",
                            "new_string": "ptr",
                            "replace_all": True,
                        },
                    }
                ]
            },
        },
        {
            "role": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "StrReplace",
                        "input": {
                            "path": "src/kernel.cpp",
                            "old_string": "i8*",
                            "new_string": "ptr",
                        },
                    }
                ]
            },
        },
    ]
    with open(log_file, "w", encoding="utf-8") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")

    candidates = LogIngestor(str(log_file)).parse()
    assert len(candidates) == 2
    assert candidates[0].replace_all is True
    assert candidates[1].replace_all is False


def test_parse_allow_multiple_flag(tmp_path):
    """IDE replace_file_content AllowMultiple maps to replace_all (R16)."""
    log_file = tmp_path / "allow_multiple.jsonl"
    step = {
        "step_index": 1,
        "tool_calls": [
            {
                "name": "replace_file_content",
                "arguments": {
                    "TargetFile": "app/db_models.py",
                    "TargetContent": "default=datetime.utcnow",
                    "ReplacementContent": "server_default=func.now()",
                    "AllowMultiple": True,
                },
            }
        ],
    }
    with open(log_file, "w", encoding="utf-8") as f:
        f.write(json.dumps(step) + "\n")

    candidates = LogIngestor(str(log_file)).parse()
    assert len(candidates) == 1
    assert candidates[0].replace_all is True


def test_parse_cursor_transcript_format(tmp_path):
    log_file = tmp_path / "cursor_transcript.jsonl"
    line = {
        "role": "assistant",
        "message": {
            "content": [
                {"type": "text", "text": "applying patch"},
                {
                    "type": "tool_use",
                    "name": "StrReplace",
                    "input": {
                        "path": "src/eval_visitor.cpp",
                        "old_string": "dead code",
                        "new_string": "// stub",
                    },
                },
            ]
        },
    }
    with open(log_file, "w", encoding="utf-8") as f:
        f.write(json.dumps(line) + "\n")

    candidates = LogIngestor(str(log_file)).parse()
    assert len(candidates) == 1
    assert candidates[0].tool_name == "StrReplace"
    assert candidates[0].source_format == "cursor/claude"

def test_parse_gemini_parts_format(tmp_path):
    log_file = tmp_path / "session.gemini"
    line = {
        "parts": [
            {
                "functionCall": {
                    "name": "replace_file_content",
                    "args": {
                        "TargetFile": "src/main.cpp",
                        "TargetContent": "x = 1",
                        "ReplacementContent": "x = 2",
                    },
                }
            }
        ]
    }
    with open(log_file, "w", encoding="utf-8") as f:
        f.write(json.dumps(line) + "\n")

    candidates = LogIngestor(str(log_file)).parse()
    assert len(candidates) == 1
    assert candidates[0].source_format == "gemini/parts"
    assert candidates[0].new_content == "x = 2"

def test_parse_anthropic_content_blocks(tmp_path):
    log_file = tmp_path / "anthropic.jsonl"
    line = {
        "type": "assistant",
        "content": [
            {
                "type": "tool_use",
                "name": "str_replace_editor",
                "input": {
                    "path": "foo.py",
                    "old_string": "a = 1",
                    "new_string": "a = 2",
                },
            }
        ],
    }
    with open(log_file, "w", encoding="utf-8") as f:
        f.write(json.dumps(line) + "\n")

    candidates = LogIngestor(str(log_file)).parse()
    assert len(candidates) == 1
    assert candidates[0].source_format == "anthropic"

def test_strip_marker_block(tmp_path):
    target = tmp_path / "main.cpp"
    target.write_text(
        "line0\n"
        "START_BLOCK\n"
        "dead1\n"
        "dead2\n"
        "END_BLOCK\n"
        "tail\n",
        encoding="utf-8",
    )
    specs = [StripSpec(start="START_BLOCK", until="END_BLOCK", replace="// stub\n")]
    apply_strips(target, specs)
    assert target.read_text(encoding="utf-8") == "line0\n// stub\nEND_BLOCK\ntail\n"


def test_parse_unified_diff_payload(tmp_path):
    log_file = tmp_path / "diff.jsonl"
    diff = (
        "--- a/src/app.py\n"
        "+++ b/src/app.py\n"
        "@@ -1,3 +1,3 @@\n"
        " def run():\n"
        "-    return 1\n"
        "+    return 2\n"
    )
    line = {"step_index": 1, "tool_calls": [{"name": "apply_patch", "arguments": {"diff": diff}}]}
    log_file.write_text(json.dumps(line) + "\n", encoding="utf-8")

    candidates = LogIngestor(str(log_file)).parse()
    assert len(candidates) == 1
    c = candidates[0]
    assert c.target_file == "src/app.py"
    assert c.action_type == "REPLACE"
    assert "return 1" in c.old_content and "return 1" not in c.new_content
    assert "return 2" in c.new_content
    assert "def run():" in c.old_content and "def run():" in c.new_content


def test_parse_apply_patch_envelope(tmp_path):
    log_file = tmp_path / "v4a.jsonl"
    envelope = (
        "*** Begin Patch\n"
        "*** Update File: lib/util.py\n"
        "@@ def helper():\n"
        "-    x = 1\n"
        "+    x = 42\n"
        "*** Add File: lib/new_mod.py\n"
        "+print('hello')\n"
        "*** Delete File: lib/old.py\n"
        "*** End Patch\n"
    )
    line = {"step_index": 2, "tool_calls": [{"name": "apply_patch", "arguments": {"input": envelope}}]}
    log_file.write_text(json.dumps(line) + "\n", encoding="utf-8")

    candidates = LogIngestor(str(log_file)).parse()
    by_action = {(c.target_file, c.action_type): c for c in candidates}

    assert ("lib/util.py", "REPLACE") in by_action
    update = by_action[("lib/util.py", "REPLACE")]
    assert "x = 1" in update.old_content and "x = 42" in update.new_content

    assert ("lib/new_mod.py", "CREATE") in by_action
    # each "+line" carries an implicit newline; absence is signalled by the
    # explicit "\\ No newline at end of file" marker (byte-fidelity fix)
    assert by_action[("lib/new_mod.py", "CREATE")].new_content == "print('hello')\n"

    assert ("lib/old.py", "DELETE") in by_action
