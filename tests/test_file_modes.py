from apatch.file_modes import normalize_file_mode


def test_normalize_file_mode_common_forms():
    assert normalize_file_mode("755") == "755"
    assert normalize_file_mode("0755") == "755"
    assert normalize_file_mode("0o755") == "755"
    assert normalize_file_mode("100755") == "755"
    assert normalize_file_mode(0o755) == "755"
    assert normalize_file_mode(755) == "755"
    assert normalize_file_mode(100755) == "755"
