"""Gate for SPEC-NOOP-ATTEST-CLI-1: `apatch attestation noop` mirrors apatch_noop_attest."""
from click.testing import CliRunner


def test_noop_attest_cli_parses_covered_by(monkeypatch):
    import apatch.runtime.runtime as rt
    from apatch.cli import cli

    captured = {}

    def fake_noop(self, covered_by, *, message=None):
        captured["covered_by"] = covered_by
        captured["message"] = message
        return {"ok": True}

    monkeypatch.setattr(rt.MutationRuntime, "noop_attest", fake_noop)
    res = CliRunner().invoke(
        cli, ["attestation", "noop", "--covered-by", "R1, R4", "--message", "m",
              "--target-dir", ".", "--json"])
    assert res.exit_code == 0, res.output
    assert captured["covered_by"] == ["R1", "R4"]  # comma list -> trimmed list
    assert captured["message"] == "m"


def test_noop_attest_cli_requires_covered_by():
    from apatch.cli import cli

    res = CliRunner().invoke(cli, ["attestation", "noop", "--target-dir", "."])
    assert res.exit_code != 0  # --covered-by is required
