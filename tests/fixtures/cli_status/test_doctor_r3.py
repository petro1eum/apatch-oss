
def test_doctor_human_includes_hygiene(tmp_path):
    from click.testing import CliRunner

    from apatch.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["doctor", "--target-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "Hygiene" in result.output
    assert "Sandbox" in result.output
    assert "Lane" in result.output
