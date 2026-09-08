"""R2 needles: apatch spec list command."""

SPEC_LIST_CMD = '''
@spec_group.command("list")
@click.option("--target-dir", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--json", "as_json", is_flag=True)
def spec_list_cmd(target_dir, as_json):
    """Discover executable spec ids from docs/specs/SPEC-*.md."""
    from apatch.cli_status import spec_list_workspace

    result = spec_list_workspace(target_dir)
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
        return
    if not result.get("ok"):
        console.print(f"[bold red]{result.get('error')}[/bold red]")
        sys.exit(1)
    specs = result.get("specs") or []
    console.print(f"[bold]Specs[/bold] ({result.get('count', len(specs))}):")
    for sid in specs:
        console.print(f"  {sid}")


'''

TEST_SPEC_LIST = '''

def test_spec_list(tmp_path):
    _write_spec(tmp_path, "SPEC-LIST-A")
    _write_spec(tmp_path, "SPEC-LIST-B")
    from apatch.cli_status import spec_list_workspace

    out = spec_list_workspace(str(tmp_path))
    assert out["ok"] is True
    assert out["count"] == 2
    assert "SPEC-LIST-A" in out["specs"]
    assert "SPEC-LIST-B" in out["specs"]

    from apatch.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["spec", "list", "--target-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "SPEC-LIST-A" in result.output
    assert "SPEC-LIST-B" in result.output
'''
