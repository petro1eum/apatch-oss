from __future__ import annotations

import json
import multiprocessing
import os


def _run_spec_writer(root: str, spec_id: str, target: str, start, applied, finish) -> None:
    try:
        os.environ["APATCH_LANE"] = spec_id.lower()
        from apatch.spec_executor import execute_next_workspace

        start.wait(20)
        first = execute_next_workspace(
            root,
            spec=spec_id,
            requirement=f"{spec_id}#R1",
            needles=[
                {
                    "action": "replace",
                    "target_file": target,
                    "find_text": "BEFORE",
                    "replace_text": f"AFTER-{spec_id}",
                    "label": spec_id,
                }
            ],
            logs_path=f"{spec_id.lower()}-patches.jsonl",
            defer_verify=True,
        )
        applied.put({"spec": spec_id, "stage": "applied", "result": first})
        if not first.get("ok"):
            return
        finish.wait(20)
        capability = first["session_capability"]
        final = execute_next_workspace(
            root,
            spec=spec_id,
            requirement=f"{spec_id}#R1",
            finalize=True,
            governed_session_id=capability["session_id"],
            session_token=capability["session_token"],
        )
        applied.put({"spec": spec_id, "stage": "final", "result": final})
    except Exception as exc:  # pragma: no cover - child diagnostic
        applied.put({"spec": spec_id, "stage": "error", "error": repr(exc)})


def test_two_disjoint_spec_write_sets_run_with_live_registry_guard(tmp_path):
    docs = tmp_path / "docs" / "specs"
    docs.mkdir(parents=True)
    targets = {
        "SPEC-APPLE": "apple/ed/runtime.py",
        "SPEC-OLANG": "o_lang/cpp/CMakeLists.txt",
    }
    for spec_id, target in targets.items():
        path = tmp_path / target
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("BEFORE\n", encoding="utf-8")
        (docs / f"{spec_id}.md").write_text(
            f"# {spec_id}\n"
            f"> **apatch artifact:** `spec:{spec_id}`\n\n"
            "## R1 Independent mutation\n\n"
            f"(verify: grep -q AFTER-{spec_id} {target})\n",
            encoding="utf-8",
        )

    ctx = multiprocessing.get_context("spawn")
    start = ctx.Event()
    finish = ctx.Event()
    results = ctx.Queue()
    processes = [
        ctx.Process(
            target=_run_spec_writer,
            args=(str(tmp_path), spec_id, target, start, results, finish),
        )
        for spec_id, target in targets.items()
    ]
    for process in processes:
        process.start()
    start.set()
    applied = [results.get(timeout=30) for _ in processes]
    assert {row["stage"] for row in applied} == {"applied"}, applied
    assert all(row["result"]["ok"] for row in applied), applied

    registry = json.loads(
        (tmp_path / ".apatch" / "write_leases.json").read_text(encoding="utf-8")
    )
    leases = list(registry["leases"].values())
    assert len(leases) == 2
    assert {tuple(cap["paths"]) for cap in leases} == {
        ("apple/ed/runtime.py",),
        ("o_lang/cpp/CMakeLists.txt",),
    }
    guard = json.loads(
        (tmp_path / ".apatch" / "write_lease.json").read_text(encoding="utf-8")
    )["capability"]
    assert guard["infrastructure_guard"] is True
    assert guard["minimum_writer_protocol"] == 2

    finish.set()
    finals = [results.get(timeout=60) for _ in processes]
    assert {row["stage"] for row in finals} == {"final"}, finals
    assert all(row["result"]["ok"] for row in finals), finals
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0
    assert (tmp_path / targets["SPEC-APPLE"]).read_text() == "AFTER-SPEC-APPLE\n"
    assert (tmp_path / targets["SPEC-OLANG"]).read_text() == "AFTER-SPEC-OLANG\n"
