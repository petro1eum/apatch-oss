import json

from apatch.apply_session import save_session
from apatch.replay import replay_session


def test_replay_no_session(tmp_path):
    result = replay_session(str(tmp_path))
    assert result["ok"] is False
    assert "no apply session" in result["error"]


def test_replay_timeline(tmp_path):
    report = tmp_path / ".apatch" / "apply_session_chunk_1.json"
    report.parent.mkdir(parents=True)
    report.write_text(
        json.dumps({"applied": 2, "failed": 0, "skipped": 0, "rolled_back": 0}),
        encoding="utf-8",
    )
    session_path = tmp_path / ".apatch" / "apply_session.json"
    save_session(
        str(session_path),
        {
            "logs_path": "p.jsonl",
            "target_dir": str(tmp_path),
            "chunks": [[1, 2]],
            "chunk_index": 1,
            "chunk_reports": [str(report)],
            "checkpoints": ["ckpt_abc"],
            "last_checkpoint": "ckpt_abc",
        },
    )
    result = replay_session(str(tmp_path), session_id="ckpt_abc")
    assert result["ok"] is True
    assert result["chunks_recorded"] == 1
    assert result["totals"]["applied"] == 2
    assert result["patch_application_order"] == [{"chunk": 0, "steps": [1, 2]}]
    assert (tmp_path / ".apatch" / "replay_log.json").is_file()


def test_replay_bad_session_id(tmp_path):
    session_path = tmp_path / ".apatch" / "apply_session.json"
    session_path.parent.mkdir(parents=True)
    save_session(
        str(session_path),
        {"checkpoints": ["ckpt_1"], "last_checkpoint": "ckpt_1", "chunks": [], "chunk_reports": []},
    )
    result = replay_session(str(tmp_path), session_id="missing")
    assert result["ok"] is False
