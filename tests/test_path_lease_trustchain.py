from __future__ import annotations

import hashlib
import multiprocessing
import subprocess
from pathlib import Path

from apatch.enforcement import load_notarized_index, record_notarized_files
from apatch.trustchain_helper import TrustChainHelper


def _index_worker(root: str, index: int) -> None:
    payload = ("value-{}".format(index)).encode()
    record_notarized_files(
        root,
        {"src/{}.py".format(index): {"sha256": hashlib.sha256(payload).hexdigest()}},
        signature="sig-{}".format(index),
    )


def _commit_worker(root: str, index: int, ready, start, results) -> None:
    helper = TrustChainHelper(root, auto_init=False)
    ready.put(index)
    start.wait(20)
    rel = "src/{}.py".format(index)
    sha = hashlib.sha256(Path(root, rel).read_bytes()).hexdigest()
    ok = helper.commit_action(
        "apatch",
        {
            "action": "parallel_commit",
            "governed_session_id": "session-{}".format(index),
            "files": {rel: {"sha256": sha}},
        },
    )
    results.put((index, ok))


def test_parallel_notarized_index_updates_have_no_lost_entries(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    processes = [ctx.Process(target=_index_worker, args=(str(tmp_path), i)) for i in range(24)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0
    index = load_notarized_index(str(tmp_path))
    assert len(index["files"]) == 24


def test_parallel_trustchain_commits_are_all_retained_and_session_bound(tmp_path, require_tc):
    subprocess.run(["tc", "init", "-o", str(tmp_path)], check=True, capture_output=True)
    (tmp_path / "src").mkdir()
    for index in range(8):
        (tmp_path / "src" / "{}.py".format(index)).write_text(str(index), encoding="utf-8")

    ctx = multiprocessing.get_context("spawn")
    ready = ctx.Queue()
    start = ctx.Event()
    results = ctx.Queue()
    processes = [
        ctx.Process(target=_commit_worker, args=(str(tmp_path), index, ready, start, results))
        for index in range(8)
    ]
    for process in processes:
        process.start()
    for _ in processes:
        ready.get(timeout=20)
    start.set()
    outcomes = [results.get(timeout=30) for _ in processes]
    for process in processes:
        process.join(timeout=30)
        assert process.exitcode == 0
    assert all(ok for _index, ok in outcomes), outcomes

    helper = TrustChainHelper(str(tmp_path), auto_init=False)
    rows = [
        row for row in helper.iter_ledger_entries()
        if (row.get("payload") or {}).get("action") == "parallel_commit"
    ]
    assert len(rows) == 8
    assert {
        (row.get("payload") or {}).get("governed_session_id") for row in rows
    } == {"session-{}".format(index) for index in range(8)}
    assert len(load_notarized_index(str(tmp_path))["files"]) == 8
