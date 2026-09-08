from __future__ import annotations

import multiprocessing
from pathlib import Path

from apatch.sandbox import acquire_lease, load_active_leases, release_lease


def _worker(root: str, index: int, ready, release) -> None:
    session_id = "load-{}".format(index)
    cap = acquire_lease(
        root,
        ["parallel/{}/result.txt".format(index)],
        tool="load_test",
        governed_session_id=session_id,
        max_seconds=60,
    )
    Path(root, "parallel", str(index)).mkdir(parents=True, exist_ok=True)
    Path(root, "parallel", str(index), "result.txt").write_text(str(index), encoding="utf-8")
    ready.put(cap["lease_id"])
    release.wait(30)
    release_lease(root, lease_id=cap["lease_id"], governed_session_id=session_id)


def test_thirty_two_disjoint_sessions_are_live_at_once(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    ready = ctx.Queue()
    release = ctx.Event()
    processes = [
        ctx.Process(target=_worker, args=(str(tmp_path), index, ready, release))
        for index in range(32)
    ]
    for process in processes:
        process.start()
    lease_ids = {ready.get(timeout=45) for _ in processes}
    assert len(lease_ids) == 32
    assert len(load_active_leases(str(tmp_path))) == 32
    release.set()
    for process in processes:
        process.join(timeout=30)
        assert process.exitcode == 0
    assert load_active_leases(str(tmp_path)) == []
    assert len(list(Path(tmp_path, "parallel").glob("*/result.txt"))) == 32
