from apatch.enforcement_messages import build_rejection_prompt, wrap_rejection_result


def test_rejection_prompt_mentions_trustchain():
    text = build_rejection_prompt(reason="never_notarized", paths=["src/a.py"])
    assert "ОТКЛОНЕНО" in text
    assert "Ed25519" in text
    assert "src/a.py" in text
    assert "apatch_apply_session" in text


def test_wrap_rejection_result_fields():
    raw = {
        "ok": False,
        "violations": [{"path": "x.py", "reason": "never_notarized"}],
    }
    out = wrap_rejection_result(raw, reason="never_notarized")
    assert out["rejected"] is True
    assert out["rejection_prompt"]
    assert out["agent_prompt"] == out["rejection_prompt"]
    assert "forbidden" in out
