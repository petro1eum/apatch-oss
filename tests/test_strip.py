import os
import json
import pytest
from apatch.strip import StripSpec, StripResult, apply_strip, apply_strips, load_strip_manifest

def test_apply_strip_single():
    lines = [
        "line 1\n",
        "line 2: start marker\n",
        "line 3\n",
        "line 4: until marker\n",
        "line 5\n"
    ]
    spec = StripSpec(
        start="start marker",
        until="until marker",
        replace="// replaced stub\n"
    )
    result = apply_strip(lines, spec)
    
    assert result.ok
    assert result.start_line == 2
    assert result.end_line == 3
    assert result.removed_lines == 2
    
    # After stripping, lines should be:
    # line 1, // replaced stub, line 4: until marker, line 5
    assert lines == [
        "line 1\n",
        "// replaced stub\n",
        "line 4: until marker\n",
        "line 5\n"
    ]

def test_analyze_strip_boundary_warns_guard_return():
    lines = [
        "export function Page() {\n",
        "  const dispatch = useDispatch();\n",
        "  if (!profile) {\n",
        "    return (\n",
        "      <div>not found</div>\n",
        "    );\n",
        "  }\n",
        "  const handleSave = () => {};\n",
        "  return (\n",
        "    <div className=\"space-y-4\">\n",
        "      {backTo && (\n",
        "        <button>Back</button>\n",
        "      )}\n",
    ]
    spec = StripSpec(
        start="const dispatch = useDispatch();",
        until="return (",
        module_kind="hook",
    )
    from apatch.strip import _find_line, _find_until_line, analyze_strip_boundary

    start_idx = _find_line(lines, spec.start)
    end_idx = _find_until_line(lines, spec.until, start=start_idx + 1)
    warnings = analyze_strip_boundary(lines, spec, start_idx, end_idx)
    assert any("early-return guard" in w for w in warnings)


def test_apply_strip_missing_marker():
    lines = [
        "line 1\n",
        "line 2\n"
    ]
    spec = StripSpec(
        start="missing marker",
        until="until marker"
    )
    result = apply_strip(lines, spec)
    assert not result.ok
    assert "marker not found" in result.error

def test_apply_strips_multiple_and_sorting(tmp_path):
    target_file = tmp_path / "code.cpp"
    content = """void process() {
    // block A start
    do_a();
    // block A end

    // block B start
    do_b();
    // block B end
}
"""
    target_file.write_text(content, encoding="utf-8")
    
    spec_a = StripSpec(
        start="// block A start",
        until="// block A end",
        replace="    // A stub\n",
        label="stub A"
    )
    spec_b = StripSpec(
        start="// block B start",
        until="// block B end",
        replace="    // B stub\n",
        label="stub B"
    )
    
    # We pass specs in order [spec_a, spec_b]
    # The system must sort them reverse by line index, i.e. apply B first, then A
    # so that the line index of A does not shift.
    lines, results = apply_strips(target_file, [spec_a, spec_b], dry_run=False)
    
    assert len(results) == 2
    assert results[0].ok
    assert results[1].ok
    
    updated_content = target_file.read_text(encoding="utf-8")
    assert "// A stub" in updated_content
    assert "// B stub" in updated_content
    assert "do_a();" not in updated_content
    assert "do_b();" not in updated_content

def test_apply_strips_dry_run(tmp_path):
    target_file = tmp_path / "code.cpp"
    content = """void process() {
    // block A start
    do_a();
    // block A end
}
"""
    target_file.write_text(content, encoding="utf-8")
    
    spec = StripSpec(
        start="// block A start",
        until="// block A end",
        replace="    // A stub\n"
    )
    
    lines, results = apply_strips(target_file, [spec], dry_run=True)
    
    assert results[0].ok
    # File should remain unchanged on disk
    assert target_file.read_text(encoding="utf-8") == content
    # Returned lines should be modified
    assert "do_a();" not in "".join(lines)
    assert "// A stub" in "".join(lines)

def test_load_strip_manifest_json(tmp_path):
    manifest_file = tmp_path / "manifest.json"
    manifest_data = {
        "strips": [
            {
                "label": "test block",
                "start": "start_here",
                "until": "end_here",
                "replace": "stub_here"
            }
        ]
    }
    manifest_file.write_text(json.dumps(manifest_data), encoding="utf-8")
    
    specs = load_strip_manifest(manifest_file)
    assert len(specs) == 1
    assert specs[0].label == "test block"
    assert specs[0].start == "start_here"
    assert specs[0].until == "end_here"
    assert specs[0].replace == "stub_here"


def test_apply_strip_normalizes_else_if_bridge():
    lines = [
        "        } else if (call->callee == \"steps_taken\") {\n",
        "            return (double)interp_.last_test_steps_;\n",
        "        } else if (call->callee == \"project_algebra\") {\n",
        "            do_algebra();\n",
        "            return 1.0;\n",
        "        } else if (call->callee == \"coreml_infer\") {\n",
        "            return 0.0;\n",
        "        }\n",
    ]
    spec = StripSpec(
        start="} else if (call->callee == \"project_algebra\")",
        until="} else if (call->callee == \"coreml_infer\")",
        replace="        // algebra — native_eval_algebra.cpp\n",
    )
    result = apply_strip(lines, spec)

    assert result.ok
    assert lines == [
        "        } else if (call->callee == \"steps_taken\") {\n",
        "            return (double)interp_.last_test_steps_;\n",
        "        }\n",
        "        // algebra — native_eval_algebra.cpp\n",
        "        else if (call->callee == \"coreml_infer\") {\n",
        "            return 0.0;\n",
        "        }\n",
    ]
    # Verify that the original, marker-bounded content was correctly captured
    assert result.removed_content == (
        "        } else if (call->callee == \"project_algebra\") {\n"
        "            do_algebra();\n"
        "            return 1.0;\n"
    )

def test_apply_strip_captures_removed_content():
    lines = [
        "line 1\n",
        "// block A\n",
        "content A\n",
        "// end A\n",
        "line 5\n"
    ]
    spec = StripSpec(
        start="// block A",
        until="// end A",
        replace="// stub A\n"
    )
    result = apply_strip(lines, spec)
    assert result.ok
    assert result.removed_content == "// block A\ncontent A\n"


def test_is_hook_until_closer_matches_empty_deps():
    from apatch.strip import _is_hook_until_closer

    assert _is_hook_until_closer("  }, []);")
    assert _is_hook_until_closer("}, [mode, scanAngle]);")
    assert not _is_hook_until_closer("  const theme = 1;")


def test_apply_strip_drops_orphan_use_effect_closer_for_hook_replace():
    lines = [
        "  // apatch: radar-overview-scan\n",
        "  useEffect(() => {\n",
        "    if (mode === 'overview') {\n",
        "      const interval = setInterval(() => {}, 40);\n",
        "      return () => clearInterval(interval);\n",
        "    }\n",
        "  }, [mode]);\n",
        "  const theme = 1;\n",
    ]
    spec = StripSpec(
        start="  // apatch: radar-overview-scan",
        until="  }, [mode]);",
        replace="  useMilitaryRadarOverviewScan({ mode, setScanAngle });\n\n",
    )
    result = apply_strip(lines, spec)
    assert result.ok
    assert "".join(lines) == (
        "  useMilitaryRadarOverviewScan({ mode, setScanAngle });\n\n"
        "  const theme = 1;\n"
    )


def test_apply_strips_batch_drops_two_orphan_closers():
    from apatch.strip import apply_strip

    lines = [
        "  // apatch: radar-clock\n",
        "  useEffect(() => {\n",
        "    const t = setInterval(() => {}, 1000);\n",
        "    return () => clearInterval(t);\n",
        "  }, []);\n",
        "  // apatch: radar-load\n",
        "  useEffect(() => {\n",
        "    void loadAll();\n",
        "  }, [loadAll]);\n",
        "  const tail = 1;\n",
    ]
    specs = [
        StripSpec(
            start="  // apatch: radar-clock",
            until="  }, []);",
            replace="  useMilitaryRadarClientClock();\n\n",
        ),
        StripSpec(
            start="  // apatch: radar-load",
            until="  }, [loadAll]);",
            replace="  useMilitaryRadarDataLoad({ loadAll });\n\n",
        ),
    ]
    def _start_line(buf, start: str) -> int:
        for i, line in enumerate(buf):
            if start in line:
                return i
        return -1

    working = list(lines)
    for spec in sorted(specs, key=lambda s: _start_line(working, s.start), reverse=True):
        apply_strip(working, spec)
    assert "".join(working) == (
        "  useMilitaryRadarClientClock();\n\n"
        "  useMilitaryRadarDataLoad({ loadAll });\n\n"
        "  const tail = 1;\n"
    )


def test_apply_strip_until_boundary_ignores_nested_closer():
    lines = [
        "  {open ? (\n",
        "    <Card>\n",
        "      {nested && (\n",
        "        <button />\n",
        "      )}\n",
        "    </Card>\n",
        "  )}\n",
        "  <Next />\n",
    ]
    spec = StripSpec(start="<Card>", until=")}", replace="<Monthly />\n")
    result = apply_strip(lines, spec)
    assert result.ok
    assert result.removed_lines == 5
    assert "".join(lines) == (
        "  {open ? (\n"
        "    <Monthly />\n"
        "  )}\n"
        "  <Next />\n"
    ) or "".join(lines) == (
        "  {open ? (\n"
        "<Monthly />\n"
        "  )}\n"
        "  <Next />\n"
    )


def test_strip_cli_export(tmp_path):
    from click.testing import CliRunner
    from apatch.cli import cli

    target_file = tmp_path / "code.cpp"
    content = """#include <iostream>
#include "omega.hpp"

void process() {
    // block A start
    interp.strict_contracts_ = false;
    this->parser_->parse();
    do_a();
    // block A end
}
"""
    target_file.write_text(content, encoding="utf-8")
    
    out_dir = tmp_path / "extracted"
    
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "strip",
            "--file", str(target_file),
            "--start", "// block A start",
            "--until", "// block A end",
            "--replace", "    // A stub - native_eval_test.cpp\n",
            "--out-dir", str(out_dir)
        ]
    )
    
    assert result.exit_code == 0
    assert "✓" in result.output
    assert "→" in result.output
    
    # Verify the code block was stripped
    stripped_content = target_file.read_text(encoding="utf-8")
    assert "do_a();" not in stripped_content
    assert "A stub" in stripped_content
    
    # Verify the file was exported and contains the correct code block
    exported_file = out_dir / "native_eval_test.cpp"
    assert exported_file.exists()
    assert exported_file.read_text(encoding="utf-8") == "    // block A start\n    interp.strict_contracts_ = false;\n    this->parser_->parse();\n    do_a();\n"

    # Verify the dependency extraction report was created and is correct
    report_file = out_dir / "extraction_report.json"
    assert report_file.exists()
    
    with open(report_file, "r", encoding="utf-8") as f:
        report_data = json.load(f)
        
    assert report_data["source_file"] == str(target_file)
    assert "#include <iostream>" in report_data["all_parent_imports"]
    assert '#include "omega.hpp"' in report_data["all_parent_imports"]
    
    assert len(report_data["extracted_blocks"]) == 1
    block_meta = report_data["extracted_blocks"][0]
    assert block_meta["filename"] == "native_eval_test.cpp"
    assert block_meta["removed_lines_count"] == 4
    assert block_meta["sha256"] != ""
    assert "native_eval_test.cpp" in block_meta["stub_replacement"]
    
    # Assert smart member accessed dependencies are parsed perfectly
    assert "interp.strict_contracts_" in block_meta["accessed_external_members"]
    assert "this->parser_" in block_meta["accessed_external_members"]

def test_strip_cli_export_python(tmp_path):
    from click.testing import CliRunner
    from apatch.cli import cli

    target_file = tmp_path / "script.py"
    content = """import os
from typing import List, Optional

def heavy_method():
    # START BLOCK
    print("Executing complex Python routine")
    # END BLOCK
    return True
"""
    target_file.write_text(content, encoding="utf-8")
    
    out_dir = tmp_path / "extracted_py"
    
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "strip",
            "--file", str(target_file),
            "--start", "# START BLOCK",
            "--until", "# END BLOCK",
            "--replace", "    # Stubbed: python_extracted.py\n",
            "--out-dir", str(out_dir)
        ]
    )
    
    assert result.exit_code == 0
    assert "✓" in result.output
    assert "→" in result.output
    
    # Verify the code block was stripped
    stripped_content = target_file.read_text(encoding="utf-8")
    assert "Executing complex Python routine" not in stripped_content
    
    # Verify the file was exported
    exported_file = out_dir / "python_extracted.py"
    assert exported_file.exists()
    assert "Executing complex Python routine" in exported_file.read_text(encoding="utf-8")

    # Verify the dependency extraction report was created with python imports
    report_file = out_dir / "extraction_report.json"
    assert report_file.exists()
    
    with open(report_file, "r", encoding="utf-8") as f:
        report_data = json.load(f)
        
    assert "import os" in report_data["all_parent_imports"]
    assert "from typing import List, Optional" in report_data["all_parent_imports"]

def test_strip_cli_export_csharp_fallback(tmp_path):
    from click.testing import CliRunner
    from apatch.cli import cli

    target_file = tmp_path / "Service.cs"
    content = """using System;
using System.Collections.Generic;

public class Service {
    public void Run() {
        // block start
        Console.WriteLine("C# Execution");
        // block end
    }
}
"""
    target_file.write_text(content, encoding="utf-8")
    
    out_dir = tmp_path / "extracted_cs"
    
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "strip",
            "--file", str(target_file),
            "--start", "// block start",
            "--until", "// block end",
            "--replace", "        // Stubbed: csharp_extracted.cs\n",
            "--out-dir", str(out_dir)
        ]
    )
    
    assert result.exit_code == 0
    
    # Verify the dependency extraction report was created with C# using statements via fallback
    report_file = out_dir / "extraction_report.json"
    assert report_file.exists()
    
    with open(report_file, "r", encoding="utf-8") as f:
        report_data = json.load(f)
        
    assert "using System;" in report_data["all_parent_imports"]
    assert "using System.Collections.Generic;" in report_data["all_parent_imports"]

def test_strip_explicit_export_option(tmp_path):
    from click.testing import CliRunner
    from apatch.cli import cli

    target_file = tmp_path / "code.cpp"
    content = """void process() {
        // block start
        do_work();
        // block end
    }
    """
    target_file.write_text(content, encoding="utf-8")
    out_dir = tmp_path / "extracted"

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "strip",
            "--file", str(target_file),
            "--start", "// block start",
            "--until", "// block end",
            "--replace", "    // A stub\n",
            "--out-dir", str(out_dir),
            "--export-filename", "explicit_target.cpp"
        ]
    )
    assert result.exit_code == 0
    exported_file = out_dir / "explicit_target.cpp"
    assert exported_file.exists()
    assert "do_work();" in exported_file.read_text(encoding="utf-8")

def test_strip_collision_resolution(tmp_path):
    from click.testing import CliRunner
    from apatch.cli import cli

    target_file = tmp_path / "code.cpp"
    # We will strip two blocks which normally resolve to "generic.cpp" dynamically
    content = """void process() {
        // block1 start
        do_work_1();
        // block1 end

        // block2 start
        do_work_2();
        // block2 end
    }
    """
    target_file.write_text(content, encoding="utf-8")

    manifest_file = tmp_path / "manifest.json"
    manifest_data = {
        "strips": [
            {
                "label": "memory_1",
                "start": "// block1 start",
                "until": "// block1 end",
                "replace": "    // generic.cpp stub\n",
                "export": "generic.cpp"
            },
            {
                "label": "memory_2",
                "start": "// block2 start",
                "until": "// block2 end",
                "replace": "    // generic.cpp stub\n",
                "export": "generic.cpp"
            }
        ]
    }
    manifest_file.write_text(json.dumps(manifest_data), encoding="utf-8")
    out_dir = tmp_path / "extracted"

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "strip",
            "--file", str(target_file),
            "--manifest", str(manifest_file),
            "--out-dir", str(out_dir)
        ]
    )
    assert result.exit_code == 0
    
    # Due to explicit collision, second file should be renamed to generic__1.cpp
    file1 = out_dir / "generic.cpp"
    file2 = out_dir / "generic__1.cpp"
    assert file1.exists()
    assert file2.exists()
    assert "do_work_1();" in file1.read_text(encoding="utf-8")
    assert "do_work_2();" in file2.read_text(encoding="utf-8")

def test_strip_enriched_metadata(tmp_path):
    from click.testing import CliRunner
    from apatch.cli import cli

    target_file = tmp_path / "code.cpp"
    content = """void process() {
        // block start
        do_work();
        // block end
    }
    """
    target_file.write_text(content, encoding="utf-8")
    out_dir = tmp_path / "extracted"

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "strip",
            "--file", str(target_file),
            "--start", "// block start",
            "--until", "// block end",
            "--replace", "    // A stub\n",
            "--out-dir", str(out_dir),
            "--export-filename", "meta_test.cpp"
        ]
    )
    assert result.exit_code == 0
    
    report_file = out_dir / "extraction_report.json"
    assert report_file.exists()
    
    with open(report_file, "r", encoding="utf-8") as f:
        report = json.load(f)
        
    block = report["extracted_blocks"][0]
    assert block["export_path"] == os.path.abspath(out_dir / "meta_test.cpp")
    assert block["line_range"] == "2-3"

def test_find_closest_line_suggestion():
    lines = [
        "void handle_some_action() {\n",
        "    do_things();\n",
        "}\n",
        "else if (call->callee == \"relax_hierarchical\") {\n",
        "    return 1.0;\n",
        "}\n"
    ]
    spec = StripSpec(
        start="else if (call->callee == \"relax_hiearchical_typo\")",
        until="}\n"
    )
    result = apply_strip(lines, spec)
    assert not result.ok
    assert "Hint: Nearest matching line found on line 4" in result.error
    assert "relax_hierarchical" in result.error

def test_strip_to_native_auto_conversion(tmp_path):
    from click.testing import CliRunner
    from apatch.cli import cli

    target_file = tmp_path / "code.cpp"
    content = """#include <iostream>
#include "omega_stl_topology.hpp"

void process() {
    // block start
    else if (call->callee == "psyche_func") {
        std::string concept = "toroidal";
        interp_.agent_counter_++;
        Interpreter::Record rec;
    }
    // block end
}
"""
    target_file.write_text(content, encoding="utf-8")
    out_dir = tmp_path / "extracted"

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "strip",
            "--file", str(target_file),
            "--start", "// block start",
            "--until", "// block end",
            "--replace", "    // A stub - native_eval_test.cpp\n",
            "--out-dir", str(out_dir),
            "--to-native", "register_psyche_ops"
        ]
    )
    assert result.exit_code == 0
    exported_file = out_dir / "native_eval_test.cpp"
    assert exported_file.exists()
    
    # Read the auto-converted native file and assert replacements
    native_code = exported_file.read_text(encoding="utf-8")
    assert "register_psyche_ops" in native_code
    assert "std::string concept_name" in native_code
    assert "agent_counter_++" in native_code
    assert "Record rec" in native_code
    assert "Interpreter::Record" not in native_code

def test_parent_imports_empty_fallback(tmp_path):
    from click.testing import CliRunner
    from apatch.cli import cli

    target_file = tmp_path / "code.cpp"
    content = """#include <iostream>
#include "coreml.hpp"

void process() {
    // block start
    do_unrelated_work();
    // block end
}
"""
    target_file.write_text(content, encoding="utf-8")
    out_dir = tmp_path / "extracted"

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "strip",
            "--file", str(target_file),
            "--start", "// block start",
            "--until", "// block end",
            "--replace", "    // A stub - native_eval_test.cpp\n",
            "--out-dir", str(out_dir)
        ]
    )
    assert result.exit_code == 0
    report_file = out_dir / "extraction_report.json"
    assert report_file.exists()
    
    with open(report_file, "r", encoding="utf-8") as f:
        report = json.load(f)
        
    # Since do_unrelated_work() matches no stems, parent_imports would have been empty [].
    # But due to the fallback mechanism, it should fall back to all parent imports!
    assert len(report["parent_imports"]) > 0
    assert "#include \"coreml.hpp\"" in report["parent_imports"]

def test_strip_dynamic_until_manifest(tmp_path):
    from click.testing import CliRunner
    from apatch.cli import cli

    target_file = tmp_path / "code.cpp"
    content = """void process() {
    // block start
    do_something();
    // some trailing comments
    } else if (call->callee == "next_block") {
    do_next();
}
"""
    target_file.write_text(content, encoding="utf-8")
    out_dir = tmp_path / "extracted"

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "strip",
            "--file", str(target_file),
            "--start", "// block start",
            "--until", "next_else_if",
            "--replace", "    // stub\n",
            "--out-dir", str(out_dir),
            "--export-filename", "generic.cpp"
        ]
    )
    assert result.exit_code == 0
    # The trailing comment '// some trailing comments' should have been trimmed from exported content
    exported = out_dir / "generic.cpp"
    assert exported.exists()
    exported_text = exported.read_text(encoding="utf-8")
    assert "do_something();" in exported_text
    assert "trailing comments" not in exported_text

def test_strip_to_native_scanner_warnings_and_strict(tmp_path):
    from click.testing import CliRunner
    from apatch.cli import cli

    target_file = tmp_path / "code.cpp"
    content = """void process() {
    // block start
    else if (call->callee == "measure_amplitude") {
        auto val = temporal_from_value(args[0]);
        eval(call->args[1]);
    }
    // block end
}
"""
    target_file.write_text(content, encoding="utf-8")
    out_dir = tmp_path / "extracted"

    runner = CliRunner()
    # 1. Run without strict, should succeed but record warnings
    result = runner.invoke(
        cli,
        [
            "strip",
            "--file", str(target_file),
            "--start", "// block start",
            "--until", "// block end",
            "--replace", "    // stub - native_eval.cpp\n",
            "--out-dir", str(out_dir),
            "--to-native", "register_ops"
        ]
    )
    assert result.exit_code == 0
    report_file = out_dir / "extraction_report.json"
    assert report_file.exists()
    with open(report_file, "r") as f:
        rep = json.load(f)
    assert "conversion_warnings" in rep
    assert len(rep["conversion_warnings"]) >= 2
    kinds = [w["kind"] for w in rep["conversion_warnings"]]
    assert "needs_eval_arg" in kinds
    assert "needs_evaluator_context" in kinds

    # 2. Re-create and run WITH strict mode -> should fail due to blocker
    target_file.write_text(content, encoding="utf-8")
    result_strict = runner.invoke(
        cli,
        [
            "strip",
            "--file", str(target_file),
            "--start", "// block start",
            "--until", "// block end",
            "--replace", "    // stub - native_eval.cpp\n",
            "--out-dir", str(out_dir),
            "--to-native", "register_ops",
            "--strict"
        ]
    )
    assert result_strict.exit_code == 1
    assert "Static Decoupling Blockers Found" in result_strict.output
    # Exported files should have been cleaned up on strict exit abort
    assert not (out_dir / "native_eval.cpp").exists()

def test_replace_keyword_safe():
    from scripts.convert_extracted_to_native import replace_keyword_safe
    
    # 1. Variables outside comments/strings should be replaced
    line1 = "std::string concept = \"concept\";"
    assert replace_keyword_safe(line1, "concept", "concept_name") == "std::string concept_name = \"concept\";"
    
    # 2. Variables inside comments should NOT be replaced
    line2 = "std::string concept = \"concept\"; // This represents a concept"
    assert replace_keyword_safe(line2, "concept", "concept_name") == "std::string concept_name = \"concept\"; // This represents a concept"
    
    # 3. Whole-line comments should be completely ignored
    line3 = "// This is a concept variable"
    assert replace_keyword_safe(line3, "concept", "concept_name") == "// This is a concept variable"


def test_strip_file_orchestration_raw_and_native_out(tmp_path):
    from click.testing import CliRunner
    from apatch.cli import cli

    target_file = tmp_path / "code.cpp"
    content = """#include <iostream>
#include "omega_stl_topology.hpp"

void process() {
    // block start
    else if (call->callee == "psyche_func") {
        std::string concept = "toroidal";
        interp_.agent_counter_++;
        Interpreter::Record rec;
    }
    // block end
}
"""
    target_file.write_text(content, encoding="utf-8")
    
    out_dir = tmp_path / "extracted_raw"
    native_out_dir = tmp_path / "native_cpp"

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "strip",
            "--file", str(target_file),
            "--start", "// block start",
            "--until", "// block end",
            "--replace", "    // A stub - native_eval_test.cpp\n",
            "--out-dir", str(out_dir),
            "--native-out-dir", str(native_out_dir),
            "--to-native", "register_psyche_ops"
        ]
    )
    assert result.exit_code == 0
    
    # Verify raw file is saved under out_dir as name.raw.cpp
    raw_file = out_dir / "native_eval_test.raw.cpp"
    assert raw_file.exists()
    assert "std::string concept = \"toroidal\";" in raw_file.read_text(encoding="utf-8")

    # Verify transformed module is saved under native_out_dir as name.cpp
    transformed_file = native_out_dir / "native_eval_test.cpp"
    assert transformed_file.exists()
    
    transformed_code = transformed_file.read_text(encoding="utf-8")
    assert "register_psyche_ops" in transformed_code
    assert "std::string concept_name" in transformed_code
    assert "agent_counter_++" in transformed_code
    assert "Record rec" in transformed_code
    assert "Interpreter::Record" not in transformed_code


def test_natives_check_cmd(tmp_path):
    from click.testing import CliRunner
    from apatch.cli import cli

    # Create two files with duplicate native registrations
    file1 = tmp_path / "native_1.cpp"
    file1.write_text('register_native("amplitude", ...);', encoding="utf-8")

    file2 = tmp_path / "native_2.cpp"
    file2.write_text('register_native("amplitude", ...);', encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "natives-check",
            str(tmp_path)
        ]
    )
    assert result.exit_code == 1
    assert "Duplicate Native Registrations Detected" in result.output
    assert "amplitude" in result.output

    # Resolve duplicates and check again
    file2.write_text('register_native("frequency", ...);', encoding="utf-8")
    result_clean = runner.invoke(
        cli,
        [
            "natives-check",
            str(tmp_path)
        ]
    )
    assert result_clean.exit_code == 0
    assert "No duplicate native registrations found" in result_clean.output


def test_strip_smart_symbol_includes(tmp_path):
    from click.testing import CliRunner
    from apatch.cli import cli

    target_file = tmp_path / "code.cpp"
    content = """#include <iostream>
void process() {
    // block start
    else if (call->callee == "topology_setup") {
        OLangTopologyFactory factory;
    }
    // block end
}
"""
    target_file.write_text(content, encoding="utf-8")
    out_dir = tmp_path / "extracted"

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "strip",
            "--file", str(target_file),
            "--start", "// block start",
            "--until", "// block end",
            "--replace", "    // A stub - native_eval_test.cpp\n",
            "--out-dir", str(out_dir),
            "--to-native", "register_topology"
        ]
    )
    assert result.exit_code == 0
    exported_file = out_dir / "native_eval_test.cpp"
    assert exported_file.exists()
    
    # Read and verify that the smart include was automatically resolved
    cpp_code = exported_file.read_text(encoding="utf-8")
    assert '#include "olang_stl_topology.hpp"' in cpp_code
    
    # Verify report JSON metadata contains resolved_includes
    report_file = out_dir / "extraction_report.json"
    assert report_file.exists()
    with open(report_file, "r") as f:
        rep = json.load(f)
    assert "resolved_includes" in rep
    assert '#include "olang_stl_topology.hpp"' in rep["resolved_includes"]
    assert rep["include_resolution"] == "symbol_table+stem+fallback"


def test_strip_emit_wiring_hints(tmp_path):
    from click.testing import CliRunner
    from apatch.cli import cli

    target_file = tmp_path / "code.cpp"
    content = """void process() {
    // block start
    else if (call->callee == "telemetry_ping") {
        telemetry::send_ping();
    }
    // block end
}
"""
    target_file.write_text(content, encoding="utf-8")
    out_dir = tmp_path / "extracted"
    patch_file = tmp_path / "patches" / "wiring.md"

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "strip",
            "--file", str(target_file),
            "--start", "// block start",
            "--until", "// block end",
            "--replace", "    // A stub - native_eval_test.cpp\n",
            "--out-dir", str(out_dir),
            "--to-native", "register_telemetry_builtins",
            "--emit-wiring", str(patch_file)
        ]
    )
    assert result.exit_code == 0
    assert patch_file.exists()
    
    # Verify Markdown wiring patch content
    md_content = patch_file.read_text(encoding="utf-8")
    assert "Apatch Native Integration Wiring Hints" in md_content
    assert "CMakeLists.txt Integration" in md_content
    assert "void register_telemetry_builtins();" in md_content
    assert "register_telemetry_builtins();" in md_content

    # Verify JSON extraction report contains wiring_hints
    report_file = out_dir / "extraction_report.json"
    assert report_file.exists()
    with open(report_file, "r") as f:
        rep = json.load(f)
    block_meta = rep["extracted_blocks"][0]
    assert "wiring_hints" in block_meta
    hints = block_meta["wiring_hints"]
    assert hints["register_func"] == "register_telemetry_builtins"
    assert hints["header_decl"] == "void register_telemetry_builtins();"
    assert hints["registration_call"] == "register_telemetry_builtins();"


def test_strip_multi_block_manifest_auto_register(tmp_path):
    from click.testing import CliRunner
    from apatch.cli import cli

    target_file = tmp_path / "code.cpp"
    content = """void process() {
    // block1 start
    else if (call->callee == "topology_ops") {
        OLangTopologyFactory factory;
    }
    // block1 end

    // block2 start
    else if (call->callee == "jit_ops") {
        omega_jit::compile();
    }
    // block2 end
}
"""
    target_file.write_text(content, encoding="utf-8")
    
    manifest_file = tmp_path / "manifest.json"
    manifest_data = {
        "strips": [
            {
                "label": "topology",
                "start": "// block1 start",
                "until": "// block1 end",
                "replace": "    // topology.cpp stub\n",
                "export": "topology.cpp",
                "register": "register_custom_topology"
            },
            {
                "label": "jit",
                "start": "// block2 start",
                "until": "// block2 end",
                "replace": "    // jit.cpp stub\n",
                "export": "jit.cpp",
                "register": "register_custom_jit"
            }
        ]
    }
    manifest_file.write_text(json.dumps(manifest_data), encoding="utf-8")
    out_dir = tmp_path / "extracted"

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "strip",
            "--file", str(target_file),
            "--manifest", str(manifest_file),
            "--out-dir", str(out_dir),
            "--to-native", "auto"
        ]
    )
    assert result.exit_code == 0
    
    # Verify that block-specific registers were correctly written into C++ modules
    topology_cpp = out_dir / "topology.cpp"
    assert topology_cpp.exists()
    assert "void Interpreter::register_custom_topology()" in topology_cpp.read_text(encoding="utf-8")
    
    jit_cpp = out_dir / "jit.cpp"
    assert jit_cpp.exists()
    assert "void Interpreter::register_custom_jit()" in jit_cpp.read_text(encoding="utf-8")


def test_apatch_phase_run_cmd(tmp_path):
    from click.testing import CliRunner
    from apatch.cli import cli

    target_file = tmp_path / "code.cpp"
    content = """void process() {
    // block start
    else if (call->callee == "ping") {
        telemetry::send_ping();
    }
    // block end
}
"""
    target_file.write_text(content, encoding="utf-8")

    manifest_file = tmp_path / "manifest.json"
    manifest_data = [
        {
            "label": "ping",
            "start": "// block start",
            "until": "// block end",
            "replace": "    // ping.cpp stub\n",
            "export": "ping.cpp",
            "register": "register_custom_ping"
        }
    ]
    manifest_file.write_text(json.dumps(manifest_data), encoding="utf-8")
    out_dir = tmp_path / "extracted"
    native_out_dir = tmp_path / "native"
    patch_file = tmp_path / "patch.md"

    runner = CliRunner()
    # 1. Run apatch phase run with verification that succeeds
    result = runner.invoke(
        cli,
        [
            "phase", "run",
            "--manifest", str(manifest_file),
            "--file", str(target_file),
            "--out-dir", str(out_dir),
            "--native-out-dir", str(native_out_dir),
            "--emit-wiring", str(patch_file),
            "--verify", "echo 'Build OK'"
        ]
    )
    assert result.exit_code == 0
    assert "Phase run" in result.output
    assert "✓ Build Verification Passed Successfully!" in result.output
    assert "PHASE EXECUTION COMPLETED SUCCESSFULLY!" in result.output
    
    # 2. Verify that files are correctly laid out
    assert patch_file.exists()
    assert (native_out_dir / "ping.cpp").exists()
    assert (out_dir / "ping.raw.cpp").exists()
