"""Rollback must not move TrustChain unless physical restore is possible."""

from apatch import workflows


def test_missing_explicit_backup_does_not_reset_trustchain(tmp_path, monkeypatch):
    calls = []

    def unexpected_reset(*args, **kwargs):
        calls.append((args, kwargs))
        return {"reset": True, "checkpoint": "wrong"}

    monkeypatch.setattr(workflows, "rollback_trustchain_checkpoint", unexpected_reset)

    result = workflows.rollback_workspace(str(tmp_path), session_id="missing-session")

    assert result["ok"] is False
    assert result["error_type"] == "BACKUPS_PRUNED"
    assert result["trustchain"]["reset"] is False
    assert result["trustchain"]["checkpoint"] is None
    assert calls == []
