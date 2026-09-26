import pytest
from apatch.csv_scope import CsvScopeError, validate_csv_replacement
from apatch.generate import generate_patches
from apatch.matcher import ASTMatcher

HEADER = "version,slug,signal,value\n"
BEFORE = HEADER + "1,a,x,old\n1,a,y,old\n1,b,x,keep\n1,c,x,keep\n"

def test_greedy_regex_rejected_before_generation(tmp_path):
    p = tmp_path / "canon.csv"
    p.write_text(BEFORE)
    with pytest.raises(CsvScopeError, match="removed slug"):
        generate_patches(find=r"(?m)^1,a,x,.*\n1,a,y,.*$",
            replace="1,a,x,new\n1,a,y,new", target_dir=str(tmp_path),
            glob_pattern="canon.csv", match_mode="regex")
    assert p.read_text() == BEFORE

def test_bounded_regex_preserves_other_slugs(tmp_path):
    p = tmp_path / "canon.csv"
    p.write_text(BEFORE)
    patches = generate_patches(find=r"(?m)^1,a,x,[^\r\n]*\n1,a,y,[^\r\n]*$",
        replace="1,a,x,new\n1,a,y,new", target_dir=str(tmp_path),
        glob_pattern="canon.csv", match_mode="regex")
    assert len(patches) == 1
    args = patches[0]["tool_calls"][0]["arguments"]
    result = ASTMatcher(str(p)).evaluate(args["TargetContent"], args["ReplacementContent"])
    assert result.success
    assert "1,b,x,keep\n1,c,x,keep\n" in result.content
    assert p.read_text() == BEFORE

def test_apply_rechecks_staged_literal_span(tmp_path):
    p = tmp_path / "canon.csv"
    p.write_text(BEFORE)
    result = ASTMatcher(str(p)).evaluate(BEFORE[len(HEADER):], "1,a,x,new\n")
    assert not result.success
    assert result.strategy == "csv-scope-rejected"
    assert result.content == BEFORE
    assert p.read_text() == BEFORE

def test_unrelated_change_is_rejected_even_without_partition_loss():
    with pytest.raises(CsvScopeError, match="unrelated"):
        validate_csv_replacement("canon.csv", BEFORE,
            BEFORE.replace("1,b,x,keep", "1,b,x,broken"), "1,a,x,new\n")

def test_full_restore_can_add_partitions_and_preserve_current_rows():
    validate_csv_replacement("canon.csv", BEFORE,
        BEFORE + "1,d,x,restored\n", BEFORE + "1,d,x,restored\n")

@pytest.mark.parametrize("after", [
    HEADER + "1,a,x,new\n",
    "version,category,signal,value\n1,a,x,new\n",
    HEADER + '1,a,x,"unterminated\n',
])
def test_invalid_full_replacement_rejected(after):
    with pytest.raises(CsvScopeError):
        validate_csv_replacement("canon.csv", BEFORE, after, after)

def test_field_only_literal_update_is_compatible(tmp_path):
    p = tmp_path / "canon.csv"
    p.write_text(BEFORE)
    result = ASTMatcher(str(p)).evaluate("old", "new", replace_all=True)
    assert result.success and "1,b,x,keep" in result.content

def test_plain_csv_and_non_csv_are_unchanged():
    validate_csv_replacement("data.csv", "id,value\n1,x\n", "", "")
    validate_csv_replacement("data.py", "slug", "", "")

def test_quoted_multiline_fields_are_records_not_lines():
    before = HEADER + '1,a,x,"one\ntwo"\n1,b,x,keep\n'
    after = before.replace("one\ntwo", "three\nfour")
    validate_csv_replacement("canon.csv", before, after, '1,a,x,"three\nfour"')
