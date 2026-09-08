from __future__ import annotations

from pathlib import Path

from apatch.agent_guidance import protocol_contract
from apatch.session_state import save_session_state
from apatch.spec_ownership import (
    ERROR_SPEC_OWNERSHIP_UNRESOLVED,
    ERROR_SPEC_TARGET_NOT_DECLARED,
    ERROR_SPEC_WORKFLOW_REQUIRED,
    authorize_spec_owned_needles,
    resolve_spec_owned_targets,
)
from apatch.workflows import generate_patch_jsonl, generate_patch_jsonl_batch


def _workspace(root: Path) -> dict:
    contracts = root / "docs" / "specs" / "slug_contracts"
    contracts.mkdir(parents=True)
    (contracts / "kran.yaml").write_text(
        "spec_generation:\n  spec_id: SPEC-KRAN-1\n", encoding="utf-8"
    )
    specs = root / "docs" / "specs"
    (specs / "SPEC-KRAN-1.md").write_text(
        "# SPEC-KRAN-1 -- Kran\n\n"
        "> **apatch artifact:** `spec:SPEC-KRAN-1`\n\n"
        "## R1 Contract\n\n(verify: true)\n",
        encoding="utf-8",
    )
    category = root / "categories" / "kran"
    category.mkdir(parents=True)
    target = category / "query.py"
    target.write_text("value = 1\n", encoding="utf-8")
    return {
        "action": "replace",
        "target_file": "categories/kran/query.py",
        "find_text": "value = 1",
        "replace_text": "value = 2",
    }


def _bind(root: Path, requirement: str) -> None:
    save_session_state(
        str(root),
        {
            "session_id": "session-1",
            "intent": "test",
            "artifacts": [{"kind": "spec", "id": requirement}],
            "ended_at": None,
        },
        force=True,
    )


def test_exact_slug_ownership_resolution(tmp_path):
    needle = _workspace(tmp_path)
    result = resolve_spec_owned_targets(str(tmp_path), [needle])

    assert result["ok"] is True
    assert result["owned"] == [
        {
            "path": "categories/kran/query.py",
            "slug": "kran",
            "spec": "SPEC-KRAN-1",
            "via": "slug_contract",
        }
    ]



def _surface_contract(root, slug="filter", **surface):
    import json

    contracts = root / "docs/specs/slug_contracts"
    contracts.mkdir(parents=True, exist_ok=True)
    data = {"spec_generation": {"spec_id": f"SPEC-{slug.upper()}-1"}}
    data.update(surface)
    (contracts / f"{slug}.yaml").write_text(json.dumps(data), encoding="utf-8")
    spec_id = data["spec_generation"]["spec_id"]
    (root / "docs/specs" / f"{spec_id}.md").write_text(
        f"# {spec_id} -- Test surface\n\n"
        f"> **apatch artifact:** `spec:{spec_id}`\n\n"
        "## R1 Contract\n\n(verify: true)\n", encoding="utf-8",
    )


def test_shared_filename_does_not_create_slug_ownership(tmp_path):
    _surface_contract(tmp_path, runtime_pipeline={
        "shared_services": ["categories/base/filter_helpers.py"],
    }, atomics={"global_sources": ["atomic/shared_filter_values.json"]})
    paths = [
        "categories/base/filter_helpers.py", "services/filter_helpers.py",
        "docs/filter-notes.md", "atomic/shared_filter_values.json",
        "categories/prefilter/query.py",
    ]
    result = resolve_spec_owned_targets(
        str(tmp_path), [{"target_file": path} for path in paths]
    )
    assert result == {"ok": True, "owned": []}


def test_canonical_slug_surfaces_remain_protected(tmp_path):
    _surface_contract(tmp_path)
    paths = [
        "categories/filter/query.py", "categories/filter/nested/query.py",
        "categories/Filter/query.py",
        "atomic/filter_query_hints.json", "atomic/filter.json",
        "config/agent_schemas/filter.json", "config/categories/filter.yaml",
        "tests/live/test_filter_slug_reality.py", "tests/test_filter.py",
        "docs/specs/slug_contracts/filter.yaml",
    ]
    result = resolve_spec_owned_targets(
        str(tmp_path), [{"target_file": path} for path in paths]
    )
    assert result["ok"] is True
    assert {row["path"] for row in result["owned"]} == set(paths)
    assert {row["spec"] for row in result["owned"]} == {"SPEC-FILTER-1"}
    denied = authorize_spec_owned_needles(
        str(tmp_path), [{"target_file": path} for path in paths]
    )
    assert denied["ok"] is False
    assert denied["generation_started"] is False


def test_declared_slug_surface_is_exact_and_not_shared_dependencies(tmp_path):
    paths = [
        "adapters/preprocess.py", "adapters/behavior.py", "adapters/build.py",
        "data/category.json", "schemas/category.json", "checks/category.py",
        "checks/reality.py",
    ]
    _surface_contract(
        tmp_path,
        runtime_pipeline={
            "category_preprocessor": paths[0], "category_behavior": paths[1],
            "query_builder": paths[2], "shared_services": ["services/common.py"],
        },
        atomics={
            "category_sources": [paths[3]], "schema_sources": [paths[4]],
            "guardrail_sources": [paths[5]], "global_sources": ["data/global.json"],
        },
        spec_generation={
            "spec_id": "SPEC-FILTER-1", "live_test_modules": [paths[6]],
        },
    )
    result = resolve_spec_owned_targets(str(tmp_path), [
        {"target_file": path}
        for path in paths + ["services/common.py", "data/global.json",
                             "adapters/preprocess.py.bak"]
    ])
    assert result["ok"] is True
    assert {row["path"] for row in result["owned"]} == set(paths)
    assert {row["spec"] for row in result["owned"]} == {"SPEC-FILTER-1"}


def test_declared_slug_surface_conflict_is_not_hidden_by_longest_slug(tmp_path):
    _surface_contract(tmp_path, atomics={"category_sources": ["data/common.json"]})
    _surface_contract(tmp_path, slug="long_filter",
                      atomics={"category_sources": ["data/common.json"]})
    result = resolve_spec_owned_targets(
        str(tmp_path), [{"target_file": "data/common.json"}]
    )
    assert result["ok"] is False
    assert result["error_type"] == ERROR_SPEC_OWNERSHIP_UNRESOLVED
    assert result["ownership_errors"][0]["path"] == "data/common.json"


def test_strict_owner_precedes_slug_surface_for_shared_file(tmp_path):
    _surface_contract(tmp_path, runtime_pipeline={
        "category_behavior": "categories/base/filter_helpers.py",
    })
    spec = tmp_path / "docs/specs/SPEC-SHARED-1.md"
    spec.write_text(
        "# SPEC-SHARED-1 -- Shared\n\n"
        "> **apatch artifact:** `spec:SPEC-SHARED-1`\n"
        "> **ownership mode:** strict\n\n"
        "## R1 Shared\n\nowns: `categories/base/filter_helpers.py`\n\n"
        "(verify: true)\n", encoding="utf-8",
    )
    result = resolve_spec_owned_targets(
        str(tmp_path), [{"target_file": "categories/base/filter_helpers.py"}]
    )
    assert result["ok"] is True
    assert {row["spec"] for row in result["owned"]} == {"SPEC-SHARED-1"}
    assert {row["via"] for row in result["owned"]} == {"strict_requirement"}


def test_malformed_declared_slug_surface_fails_closed(tmp_path):
    for value in ["../outside.py", "/outside.py", "data/*.json",
                  "data/../outside.py", "data\\outside.py", "", 42]:
        _surface_contract(tmp_path, atomics={"category_sources": [value]})
        result = resolve_spec_owned_targets(
            str(tmp_path), [{"target_file": "categories/filter/query.py"}]
        )
        assert result["ok"] is False, value
        assert result["error_type"] == ERROR_SPEC_OWNERSHIP_UNRESOLVED


def test_yml_slug_contract_uses_its_explicit_spec_id(tmp_path):
    _surface_contract(tmp_path, spec_generation={"spec_id": "SPEC-CUSTOM-1"})
    contract = tmp_path / "docs/specs/slug_contracts/filter.yaml"
    contract.rename(contract.with_suffix(".yml"))
    result = resolve_spec_owned_targets(
        str(tmp_path), [{"target_file": "categories/filter/query.py"}]
    )
    assert result["ok"] is True
    assert {row["spec"] for row in result["owned"]} == {"SPEC-CUSTOM-1"}


def test_slug_ownership_checks_both_rename_paths(tmp_path):
    _surface_contract(tmp_path)
    for source, target in [
        ("categories/filter/query.py", "services/query.py"),
        ("services/query.py", "categories/filter/query.py"),
    ]:
        result = authorize_spec_owned_needles(str(tmp_path), [{
            "action": "rename", "source_file": source, "target_file": target,
        }])
        assert result["ok"] is False
        assert result["generation_started"] is False
        assert result["required_specs"] == ["SPEC-FILTER-1"]


def test_shared_partition_reports_only_conflicting_targets(tmp_path):
    _surface_contract(tmp_path)
    _surface_contract(tmp_path, slug="other")
    good = "categories/other/query.py"
    bad = "categories/filter/query.py"
    save_session_state(str(tmp_path), {
        "session_id": "session-shared", "intent": "diagnose exact partition",
        "artifacts": [
            {"kind": "spec", "id": "SPEC-FILTER-1#R1"},
            {"kind": "spec", "id": "SPEC-OTHER-1#R1"},
        ],
        "artifact_files": {
            "spec:SPEC-FILTER-1#R1": ["services/shared.py"],
            "spec:SPEC-OTHER-1#R1": [good, bad],
        },
        "ended_at": None,
    }, force=True)
    result = authorize_spec_owned_needles(
        str(tmp_path),
        [{"target_file": path} for path in [good, bad, "services/shared.py"]],
        created_by_tool="apatch_spec_run_multi:shared_maintenance",
    )
    assert result["ok"] is False
    assert result["generation_started"] is False
    assert result["error_type"] == ERROR_SPEC_WORKFLOW_REQUIRED
    assert [row["path"] for row in result["rejected_targets"]] == [bad]
    assert result["partition_conflicts"] == [{
        "path": bad, "expected_spec": "SPEC-FILTER-1",
        "actual_requirement": "SPEC-OTHER-1#R1",
    }]



def test_invalid_surface_cannot_hide_other_declared_paths(tmp_path):
    _surface_contract(tmp_path, atomics={
        "category_sources": ["adapters/owned.py", "../escape.py"],
    })
    result = resolve_spec_owned_targets(
        str(tmp_path), [{"target_file": "adapters/owned.py"}]
    )
    assert result["ok"] is False
    assert result["generation_started"] is False
    assert result["error_type"] == ERROR_SPEC_OWNERSHIP_UNRESOLVED


def test_duplicate_yaml_and_yml_contracts_fail_closed(tmp_path):
    _surface_contract(tmp_path)
    contract = tmp_path / "docs/specs/slug_contracts/filter.yaml"
    contract.with_suffix(".yml").write_bytes(contract.read_bytes())
    result = resolve_spec_owned_targets(
        str(tmp_path), [{"target_file": "categories/filter/query.py"}]
    )
    assert result["ok"] is False
    assert result["error_type"] == ERROR_SPEC_OWNERSHIP_UNRESOLVED


def test_shared_helper_partition_is_not_reassigned_by_its_filename(tmp_path):
    _surface_contract(tmp_path)
    _surface_contract(tmp_path, slug="other")
    category = "categories/filter/query.py"
    shared = "categories/base/filter_helpers.py"
    save_session_state(str(tmp_path), {
        "session_id": "session-shared", "intent": "shared helper maintenance",
        "artifacts": [
            {"kind": "spec", "id": "SPEC-FILTER-1#R1"},
            {"kind": "spec", "id": "SPEC-OTHER-1#R1"},
        ],
        "artifact_files": {
            "spec:SPEC-FILTER-1#R1": [category],
            "spec:SPEC-OTHER-1#R1": [shared],
        },
        "ended_at": None,
    }, force=True)
    result = authorize_spec_owned_needles(
        str(tmp_path), [{"target_file": path} for path in [category, shared]],
        created_by_tool="apatch_spec_run_multi:shared_maintenance",
    )
    assert result["ok"] is True
    assert result["authorization"] == "partitioned_multi_spec_requirements"
    assert {row["path"] for row in result["owned"]} == {category}


def test_unbound_owned_mutation_is_rejected_before_generation(tmp_path):
    needle = _workspace(tmp_path)
    out_path = tmp_path / ".apatch" / "remote" / "patches.jsonl"

    result = generate_patch_jsonl_batch(
        needles=[needle],
        target_dir=str(tmp_path),
        out_path=str(out_path),
        created_by_tool="apatch_remote_task_run",
    )

    assert result["ok"] is False
    assert result["error_type"] == ERROR_SPEC_WORKFLOW_REQUIRED
    assert result["generation_started"] is False
    assert result["required_specs"] == ["SPEC-KRAN-1"]
    assert not out_path.exists()

    single_path = tmp_path / ".apatch" / "single" / "patches.jsonl"
    single = generate_patch_jsonl(
        find_text="value = 1",
        replace_text="value = 2",
        target_dir=str(tmp_path),
        glob_pattern="categories/kran/query.py",
        out_path=str(single_path),
    )
    assert single["ok"] is False
    assert single["error_type"] == ERROR_SPEC_WORKFLOW_REQUIRED
    assert not single_path.exists()


def test_exact_requirement_binding_allows_and_wrong_spec_rejects(tmp_path):
    needle = _workspace(tmp_path)
    _bind(tmp_path, "SPEC-KRAN-1#R1")

    allowed = authorize_spec_owned_needles(
        str(tmp_path), [needle], created_by_tool="apatch_execute_next"
    )
    fix_forward = authorize_spec_owned_needles(
        str(tmp_path), [needle], created_by_tool="apatch_remote_task_run:fix_forward"
    )
    generic = authorize_spec_owned_needles(
        str(tmp_path), [needle], created_by_tool="apatch_generate_batch"
    )

    assert allowed["ok"] is True
    assert allowed["requirement_token"] == "SPEC-KRAN-1#R1"
    assert fix_forward["ok"] is True
    assert fix_forward["requirement_token"] == "SPEC-KRAN-1#R1"
    assert generic["ok"] is False

    (tmp_path / "docs" / "specs" / "slug_contracts" / "shurup.yaml").write_text(
        "spec_generation:\n  spec_id: SPEC-SHURUP-1\n", encoding="utf-8"
    )
    bootstrap = {
        "action": "create",
        "target_file": "docs/specs/SPEC-SHURUP-1.md",
        "content": "# SPEC-SHURUP-1 -- Shurup\n",
    }
    _bind(tmp_path, "SPEC-SHURUP-1#R1")
    bootstrapped = authorize_spec_owned_needles(
        str(tmp_path), [bootstrap], created_by_tool="apatch_remote_task_run:requirement"
    )
    assert bootstrapped["ok"] is True
    assert bootstrapped["authorization"] == "spec_requirement"
    assert bootstrapped["owned"] == [
        {
            "path": "docs/specs/SPEC-SHURUP-1.md",
            "spec": "SPEC-SHURUP-1",
            "via": "spec_file",
        }
    ]

    from apatch.remote.orchestrator import _build_steps

    bootstrap_steps = _build_steps(
        "bootstrap shurup",
        {
            "requirement": "SPEC-SHURUP-1#R1",
            "needles": [
                {
                    **bootstrap,
                    "content": (
                        "# SPEC-SHURUP-1 -- Shurup\n\n"
                        "> **apatch artifact:** `spec:SPEC-SHURUP-1`\n\n"
                        "## R1 Route boundary\n\n(verify: true)\n"
                    ),
                }
            ],
        },
        None,
    )
    start_args = bootstrap_steps[1][1]
    assert "requirement" not in start_args
    assert start_args["artifacts"] == ["spec-bootstrap:SPEC-SHURUP-1#R1"]

    malformed_steps = _build_steps(
        "malformed bootstrap",
        {"requirement": "SPEC-SHURUP-1#R1", "needles": [bootstrap]},
        None,
    )
    assert malformed_steps[1][1]["requirement"] == "SPEC-SHURUP-1#R1"

    _bind(tmp_path, "SPEC-OTHER-1#R1")
    wrong = authorize_spec_owned_needles(
        str(tmp_path), [needle], created_by_tool="apatch_execute_next"
    )
    assert wrong["ok"] is False
    assert wrong["required_specs"] == ["SPEC-KRAN-1"]


def test_partitioned_multi_spec_channel_checks_exact_path_owner(tmp_path):
    first = _workspace(tmp_path)
    contracts = tmp_path / "docs" / "specs" / "slug_contracts"
    specs = tmp_path / "docs" / "specs"
    (contracts / "mufta.yaml").write_text(
        "spec_generation:\n  spec_id: SPEC-MUFTA-1\n", encoding="utf-8"
    )
    (specs / "SPEC-MUFTA-1.md").write_text(
        "# SPEC-MUFTA-1 -- Mufta\n\n"
        "> **apatch artifact:** `spec:SPEC-MUFTA-1`\n\n"
        "## R2 Contract\n\n(verify: true)\n",
        encoding="utf-8",
    )
    target = tmp_path / "categories" / "mufta" / "query.py"
    target.parent.mkdir(parents=True)
    target.write_text("value = 1\n", encoding="utf-8")
    second = {
        "action": "replace",
        "target_file": "categories/mufta/query.py",
        "find_text": "value = 1",
        "replace_text": "value = 2",
    }
    save_session_state(
        str(tmp_path),
        {
            "session_id": "session-shared",
            "intent": "partitioned maintenance",
            "artifacts": [
                {"kind": "spec", "id": "SPEC-KRAN-1#R1"},
                {"kind": "spec", "id": "SPEC-MUFTA-1#R2"},
            ],
            "artifact_files": {
                "spec:SPEC-KRAN-1#R1": ["categories/kran/query.py"],
                "spec:SPEC-MUFTA-1#R2": ["categories/mufta/query.py"],
            },
            "ended_at": None,
        },
        force=True,
    )
    allowed = authorize_spec_owned_needles(
        str(tmp_path), [first, second],
        created_by_tool="apatch_spec_run_multi:shared_maintenance",
    )
    assert allowed["ok"] is True
    assert allowed["authorization"] == "partitioned_multi_spec_requirements"

    save_session_state(
        str(tmp_path),
        {
            "session_id": "session-shared",
            "intent": "forged partition",
            "artifacts": [
                {"kind": "spec", "id": "SPEC-KRAN-1#R1"},
                {"kind": "spec", "id": "SPEC-MUFTA-1#R2"},
            ],
            "artifact_files": {
                "spec:SPEC-KRAN-1#R1": ["categories/mufta/query.py"],
                "spec:SPEC-MUFTA-1#R2": ["categories/kran/query.py"],
            },
            "ended_at": None,
        },
        force=True,
    )
    forged = authorize_spec_owned_needles(
        str(tmp_path), [first, second],
        created_by_tool="apatch_spec_run_multi:shared_maintenance",
    )
    assert forged["ok"] is False
    assert forged["error_type"] == ERROR_SPEC_WORKFLOW_REQUIRED


def test_fix_forward_repairs_only_the_malformed_contract(tmp_path):
    needle = _workspace(tmp_path)
    contract = tmp_path / "docs" / "specs" / "slug_contracts" / "kran.yaml"
    contract.write_text("spec_generation:\n  spec_id: [\n", encoding="utf-8")
    repair = {
        "action": "replace",
        "target_file": "docs/specs/slug_contracts/kran.yaml",
        "find_text": "spec_id: [",
        "replace_text": "spec_id: SPEC-KRAN-1",
    }
    _bind(tmp_path, "SPEC-KRAN-1#R1")

    allowed = authorize_spec_owned_needles(
        str(tmp_path),
        [repair],
        created_by_tool="apatch_remote_task_run:fix_forward",
    )
    assert allowed["ok"] is True
    assert allowed["owned"] == [
        {
            "path": "docs/specs/slug_contracts/kran.yaml",
            "slug": "kran",
            "spec": "SPEC-KRAN-1",
            "via": "active_fix_forward_recovery",
        }
    ]

    ordinary = authorize_spec_owned_needles(
        str(tmp_path), [repair], created_by_tool="apatch_execute_next"
    )
    assert ordinary["ok"] is False
    assert ordinary["error_type"] == ERROR_SPEC_OWNERSHIP_UNRESOLVED

    category = authorize_spec_owned_needles(
        str(tmp_path),
        [needle],
        created_by_tool="apatch_remote_task_run:fix_forward",
    )
    assert category["ok"] is False
    assert category["error_type"] == ERROR_SPEC_OWNERSHIP_UNRESOLVED


def test_longest_slug_owner_wins_over_prefix_collision(tmp_path):
    contracts = tmp_path / "docs" / "specs" / "slug_contracts"
    contracts.mkdir(parents=True)
    specs = tmp_path / "docs" / "specs"
    (contracts / "regulyator.yaml").write_text(
        "spec_generation:\n  spec_id: SPEC-REGULYATOR-1\n", encoding="utf-8"
    )
    (contracts / "regulyator_davleniya.yaml").write_text(
        "spec_generation:\n  spec_id: SPEC-REGULYATOR-DAVLENIYA-1\n",
        encoding="utf-8",
    )
    (specs / "SPEC-REGULYATOR-DAVLENIYA-1.md").write_text(
        "# SPEC-REGULYATOR-DAVLENIYA-1 -- Regulyator davleniya\n\n"
        "> **apatch artifact:** `spec:SPEC-REGULYATOR-DAVLENIYA-1`\n\n"
        "## R1 Contract\n\n(verify: true)\n",
        encoding="utf-8",
    )

    result = resolve_spec_owned_targets(
        str(tmp_path),
        [
            {
                "action": "replace",
                "target_file": "docs/specs/slug_contracts/regulyator_davleniya.yaml",
                "find_text": "old",
                "replace_text": "new",
            },
            {
                "action": "replace",
                "target_file": "categories/regulyator_davleniya/query.py",
                "find_text": "old",
                "replace_text": "new",
            },
        ],
    )

    assert result["ok"] is True
    assert {(row["path"], row["spec"]) for row in result["owned"]} == {
        (
            "docs/specs/slug_contracts/regulyator_davleniya.yaml",
            "SPEC-REGULYATOR-DAVLENIYA-1",
        ),
        (
            "categories/regulyator_davleniya/query.py",
            "SPEC-REGULYATOR-DAVLENIYA-1",
        ),
    }


def test_longest_slug_owner_wins_over_suffix_collision(tmp_path):
    contracts = tmp_path / "docs" / "specs" / "slug_contracts"
    contracts.mkdir(parents=True)
    specs = tmp_path / "docs" / "specs"
    (contracts / "termostat.yaml").write_text(
        "spec_generation:\n  spec_id: SPEC-TERMOSTAT-1\n", encoding="utf-8"
    )
    (contracts / "element_termostat.yaml").write_text(
        "spec_generation:\n  spec_id: SPEC-ELEMENT-TERMOSTAT-1\n",
        encoding="utf-8",
    )
    (specs / "SPEC-ELEMENT-TERMOSTAT-1.md").write_text(
        "# SPEC-ELEMENT-TERMOSTAT-1 -- Element termostat\n\n"
        "> **apatch artifact:** `spec:SPEC-ELEMENT-TERMOSTAT-1`\n\n"
        "## R1 Contract\n\n(verify: true)\n",
        encoding="utf-8",
    )

    result = resolve_spec_owned_targets(
        str(tmp_path),
        [
            {
                "action": "replace",
                "target_file": "atomic/element_termostat_query_hints.json",
                "find_text": "old",
                "replace_text": "new",
            },
            {
                "action": "create",
                "target_file": "tests/unit/test_element_termostat_rtr7090.py",
                "content": "pass\n",
            },
        ],
    )

    assert result["ok"] is True
    assert {(row["path"], row["spec"]) for row in result["owned"]} == {
        (
            "atomic/element_termostat_query_hints.json",
            "SPEC-ELEMENT-TERMOSTAT-1",
        ),
        (
            "tests/unit/test_element_termostat_rtr7090.py",
            "SPEC-ELEMENT-TERMOSTAT-1",
        ),
    }


def test_remote_worker_propagates_spec_workflow_required(tmp_path, monkeypatch):
    from apatch.remote import worker

    needle = _workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = worker.dispatch(
        {
            "operation": "apatch_generate_batch",
            "protocol_version": worker.PROTOCOL_VERSION,
            "arguments": {"plan": {"needles": [needle]}},
        }
    )

    assert result["ok"] is False
    assert result["error_type"] == ERROR_SPEC_WORKFLOW_REQUIRED
    assert result["generation_started"] is False
    assert not (tmp_path / ".apatch" / "remote" / "patches.jsonl").exists()


def test_agent_guidance_exposes_hard_spec_gate():
    contract = protocol_contract()
    gate = contract["spec_ownership_gate"]

    assert gate["enforcement"] == "pre-generation hard fail"
    assert "SPEC_WORKFLOW_REQUIRED" in gate["failure"]
    text = Path("docs/AGENTS.template.md").read_text(encoding="utf-8")
    assert "SPEC-owned files" in text
    assert "SPEC_WORKFLOW_REQUIRED" in text

    # A gate that reads as unconditional is a gate a reader relies on without
    # declaring it: both surfaces must say ownership is opt-in and how to opt in.
    owned = gate["what_is_owned"]
    assert "opt-in" in owned
    assert "ownership mode:** strict" in owned and "owns:" in owned
    assert "unowned" in owned
    assert "bounded" in owned and "shared_services" in owned
    assert "global_sources" in owned and "shared filename" in owned
    assert "resolve_spec_owned_targets" in gate["authority"]
    assert "ownership mode:** strict" in text and "owns:" in text


def test_declared_requirement_ownership_resolves_exact_paths_and_prefixes(tmp_path):
    specs = tmp_path / "docs" / "specs"
    specs.mkdir(parents=True)
    (specs / "SPEC-OLANG-1.md").write_text(
        "# SPEC-OLANG-1 -- OLang\n\n"
        "> **apatch artifact:** `spec:SPEC-OLANG-1`\n"
        "> **ownership mode:** strict\n\n"
        "## R1 Runtime\n\n"
        "owns: `o_lang/runtime.cpp`, `o_lang/tests/**`\n\n"
        "(verify: true)\n",
        encoding="utf-8",
    )

    result = resolve_spec_owned_targets(
        str(tmp_path),
        [
            {
                "action": "replace",
                "target_file": "o_lang/runtime.cpp",
                "find_text": "old",
                "replace_text": "new",
            },
            {
                "action": "create",
                "target_file": "o_lang/tests/test_runtime.omega",
                "content": "assert true\n",
            },
        ],
    )

    assert result["ok"] is True
    assert {
        (row["path"], row["spec"], row.get("requirement"), row.get("via"))
        for row in result["owned"]
    } == {
        (
            "o_lang/runtime.cpp",
            "SPEC-OLANG-1",
            "SPEC-OLANG-1#R1",
            "strict_requirement",
        ),
        (
            "o_lang/tests/test_runtime.omega",
            "SPEC-OLANG-1",
            "SPEC-OLANG-1#R1",
            "strict_requirement",
        ),
    }

    # A module that implements several requirements is owned by all of them, and any
    # one authorizes the write. Forcing one artificial owner per file would make the
    # declaration lie about the code; only a claim spanning two SPECs is ambiguous.
    (specs / "SPEC-SHARED-1.md").write_text(
        "# SPEC-SHARED-1 -- Shared surface\n\n"
        "> **apatch artifact:** `spec:SPEC-SHARED-1`\n"
        "> **ownership mode:** strict\n\n"
        "## R1 First\n\n"
        "owns: `shared/core.py`\n\n"
        "(verify: true)\n\n"
        "## R2 Second\n\n"
        "owns: `shared/core.py`\n\n"
        "(verify: true)\n",
        encoding="utf-8",
    )
    shared = resolve_spec_owned_targets(
        str(tmp_path),
        [
            {
                "action": "replace",
                "target_file": "shared/core.py",
                "find_text": "old",
                "replace_text": "new",
            }
        ],
    )
    assert shared["ok"] is True
    assert {row.get("requirement") for row in shared["owned"]} == {
        "SPEC-SHARED-1#R1",
        "SPEC-SHARED-1#R2",
    }

    (specs / "SPEC-OTHER-1.md").write_text(
        "# SPEC-OTHER-1 -- Other\n\n"
        "> **apatch artifact:** `spec:SPEC-OTHER-1`\n"
        "> **ownership mode:** strict\n\n"
        "## R1 Collision\n\n"
        "owns: `o_lang/runtime.cpp`\n\n"
        "(verify: true)\n",
        encoding="utf-8",
    )
    ambiguous = resolve_spec_owned_targets(
        str(tmp_path),
        [
            {
                "action": "replace",
                "target_file": "o_lang/runtime.cpp",
                "find_text": "old",
                "replace_text": "new",
            }
        ],
    )
    assert ambiguous["ok"] is False
    assert ambiguous["error_type"] == ERROR_SPEC_OWNERSHIP_UNRESOLVED

    (specs / "SPEC-OTHER-1.md").write_text(
        "# SPEC-OTHER-1 -- Other\n\n"
        "> **apatch artifact:** `spec:SPEC-OTHER-1`\n"
        "> **ownership mode:** strict\n\n"
        "## R1 Invalid\n\n"
        "owns: `../escape.py`\n\n"
        "(verify: true)\n",
        encoding="utf-8",
    )
    malformed = resolve_spec_owned_targets(str(tmp_path), [])
    assert malformed["ok"] is False
    assert malformed["error_type"] == ERROR_SPEC_OWNERSHIP_UNRESOLVED


def test_strict_requirement_rejects_undeclared_target_before_generation(tmp_path):
    specs = tmp_path / "docs" / "specs"
    specs.mkdir(parents=True)
    (specs / "SPEC-OLANG-1.md").write_text(
        "# SPEC-OLANG-1 -- OLang\n\n"
        "> **apatch artifact:** `spec:SPEC-OLANG-1`\n"
        "> **ownership mode:** strict\n\n"
        "## R1 Runtime\n\n"
        "owns: `o_lang/runtime.cpp`\n\n"
        "(verify: true)\n\n"
        "## R2 Tests\n\n"
        "owns: `o_lang/tests/**`\n\n"
        "(verify: true)\n",
        encoding="utf-8",
    )
    runtime = tmp_path / "o_lang" / "runtime.cpp"
    runtime.parent.mkdir(parents=True)
    runtime.write_text("value = 1\n", encoding="utf-8")
    _bind(tmp_path, "SPEC-OLANG-1#R1")

    allowed = {
        "action": "replace",
        "target_file": "o_lang/runtime.cpp",
        "find_text": "value = 1",
        "replace_text": "value = 2",
    }
    authorized = authorize_spec_owned_needles(
        str(tmp_path), [allowed], created_by_tool="apatch_execute_next"
    )
    assert authorized["ok"] is True

    out_path = tmp_path / ".apatch" / "strict" / "patches.jsonl"
    extra = {
        "action": "create",
        "target_file": "o_lang/stdlib/business/tender.omega",
        "content": "on hidden_engine() { return true }\n",
    }
    rejected = generate_patch_jsonl_batch(
        needles=[allowed, extra],
        target_dir=str(tmp_path),
        out_path=str(out_path),
        created_by_tool="apatch_execute_next",
    )
    assert rejected["ok"] is False
    assert rejected["error_type"] == ERROR_SPEC_TARGET_NOT_DECLARED
    assert rejected["generation_started"] is False
    assert rejected["rejected_targets"] == [
        "o_lang/stdlib/business/tender.omega"
    ]
    assert not out_path.exists()

    wrong_requirement = {
        "action": "create",
        "target_file": "o_lang/tests/test_runtime.omega",
        "content": "assert true\n",
    }
    wrong = authorize_spec_owned_needles(
        str(tmp_path), [wrong_requirement], created_by_tool="apatch_execute_next"
    )
    assert wrong["ok"] is False
    assert wrong["error_type"] == ERROR_SPEC_TARGET_NOT_DECLARED


def _bind_bootstrap(root: Path, requirement: str) -> None:
    save_session_state(
        str(root),
        {
            "session_id": "session-boot",
            "intent": "author a fresh contract lineage",
            "artifacts": [{"kind": "spec-bootstrap", "id": requirement}],
            "ended_at": None,
        },
        force=True,
    )


def test_local_bootstrap_session_may_create_its_own_new_spec(tmp_path):
    _workspace(tmp_path)
    needle = {
        "action": "create",
        "target_file": "docs/specs/SPEC-FRESH-1.md",
        "content": (
            "# SPEC-FRESH-1 -- Fresh\n\n"
            "> **apatch artifact:** `spec:SPEC-FRESH-1`\n\n"
            "## R0 Gate\n\n(verify: true)\n"
        ),
    }
    _bind_bootstrap(tmp_path, "SPEC-FRESH-1#R0")

    result = authorize_spec_owned_needles(
        str(tmp_path), [needle], created_by_tool="apatch_generate_batch"
    )

    assert result["ok"] is True, result
    assert result["authorization"] == "spec_bootstrap"
    assert result["owned"] == [
        {"path": "docs/specs/SPEC-FRESH-1.md", "spec": "SPEC-FRESH-1", "via": "spec_file"}
    ]


def test_bootstrap_binding_does_not_unlock_existing_or_foreign_specs(tmp_path):
    _workspace(tmp_path)
    (tmp_path / "docs" / "specs" / "SPEC-FRESH-1.md").write_text(
        "# SPEC-FRESH-1 -- Fresh\n", encoding="utf-8"
    )
    _bind_bootstrap(tmp_path, "SPEC-FRESH-1#R0")

    existing = {
        "action": "replace",
        "target_file": "docs/specs/SPEC-FRESH-1.md",
        "find_text": "Fresh",
        "replace_text": "Fresher",
    }
    denied = authorize_spec_owned_needles(
        str(tmp_path), [existing], created_by_tool="apatch_generate_batch"
    )
    assert denied["ok"] is False
    assert denied["error_type"] == ERROR_SPEC_WORKFLOW_REQUIRED

    foreign = {
        "action": "create",
        "target_file": "docs/specs/SPEC-OTHER-9.md",
        "content": "# SPEC-OTHER-9 -- Other\n",
    }
    denied_foreign = authorize_spec_owned_needles(
        str(tmp_path), [foreign], created_by_tool="apatch_generate_batch"
    )
    assert denied_foreign["ok"] is False
    assert denied_foreign["error_type"] == ERROR_SPEC_WORKFLOW_REQUIRED

    plain = {
        "action": "create",
        "target_file": "docs/specs/SPEC-PLAIN-1.md",
        "content": "# SPEC-PLAIN-1 -- Plain\n",
    }
    _bind(tmp_path, "SPEC-PLAIN-1#R0")
    denied_plain = authorize_spec_owned_needles(
        str(tmp_path), [plain], created_by_tool="apatch_generate_batch"
    )
    assert denied_plain["ok"] is False
    assert denied_plain["error_type"] == ERROR_SPEC_WORKFLOW_REQUIRED
