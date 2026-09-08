from apatch.console.state import load_console_state, save_console_state


def test_console_state_roundtrip(tmp_path):
    save_console_state(str(tmp_path), {"logs_path": "/tmp/p.jsonl", "last_message": "ok"})
    state = load_console_state(str(tmp_path))
    assert state["logs_path"] == "/tmp/p.jsonl"
    assert state["last_message"] == "ok"
