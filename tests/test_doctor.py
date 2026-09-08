from apatch.doctor import init_consumer, resolve_mcp_command, run_doctor
from apatch.enforcement import write_enforcement_config
from apatch.trustchain_helper import TrustChainHelper


def test_run_doctor_includes_mcp_command(tmp_path):
    info = run_doctor(str(tmp_path))
    assert "mcp_command" in info
    assert resolve_mcp_command() == info["mcp_command"] or info["mcp_command"] is None


def test_run_doctor(tmp_path):
    info = run_doctor(str(tmp_path))
    assert "version" in info
    assert "tree_sitter_grammars" in info
    assert "example_commands" in info
    if info["mcp_profile"] == "compact":
        assert "apatch_execute_next" in info["agent_protocol"]["mass_refactor"]
        assert "apatch_apply_session" not in info["agent_protocol"]["mass_refactor"]
    else:
        assert "apatch_simulate" in info["agent_protocol"]["mass_refactor"]
        assert "apatch_apply_session" in info["agent_protocol"]["mass_refactor"]
    assert info["agent_protocol"]["checkpoint_after_every_chunk"] is True
    assert "enforcement" in info
    assert info["trustchain"]["mode"] == "audit_pending"
    assert info["trustchain"]["behaviors"]["no_trustchain_allowed"] is True


def test_doctor_surfaces_incompatible_avatar_runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "apatch.avatar_delivery.avatar_runtime_compatibility",
        lambda: {
            "ok": False,
            "status": "dependency_incompatible",
            "installed_version": "0.4.0",
            "python_executable": "/opt/homebrew/bin/python3",
            "module_path": "/tmp/stale/avatar_contract/__init__.py",
            "missing_symbols": ["build_work_review_package"],
        },
    )

    info = run_doctor(str(tmp_path))

    assert info["avatar_contract"]["installed_version"] == "0.4.0"
    warning = next(
        item
        for item in info["warnings"]
        if item.startswith("AVATAR CONTRACT INCOMPATIBLE:")
    )
    assert "build_work_review_package" in warning
    assert "restart MCP" in warning


def test_doctor_trustchain_audit_mode(tmp_path):
    TrustChainHelper(str(tmp_path), auto_init=True)
    info = run_doctor(str(tmp_path))
    assert info["trustchain"]["mode"] == "audit"
    assert info["trustchain"]["active"] is True
    assert info["trustchain"]["behaviors"]["rollback_on_notarization_failure"] is False


def test_doctor_trustchain_enforce_mode(tmp_path):
    write_enforcement_config(str(tmp_path))
    TrustChainHelper(str(tmp_path), auto_init=True)
    info = run_doctor(str(tmp_path))
    assert info["trustchain"]["mode"] == "enforce"
    assert info["trustchain"]["enforcement_active"] is True
    assert info["trustchain"]["behaviors"]["no_trustchain_allowed"] is False
    assert info["trustchain"]["behaviors"]["incremental_notarization"] is True
    assert info["trustchain"]["upgrade_to_enforce"] is None



def test_doctor_human_includes_hygiene(tmp_path):
    from click.testing import CliRunner

    from apatch.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["doctor", "--target-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "Hygiene" in result.output
    assert "Sandbox" in result.output
    assert "Lane" in result.output


def test_init_consumer(tmp_path):
    created = init_consumer(str(tmp_path))
    assert any("apatch.example.json" in p for p in created)
    assert (tmp_path / "manifests" / "README.md").exists()
    assert (tmp_path / "AGENTS.md").exists()


def test_init_consumer_olang_profile_protects_full_language_surface(tmp_path, monkeypatch):
    import json

    from apatch.sandbox import evaluate_write_policy, load_sandbox_config

    from click.testing import CliRunner

    from apatch.cli import cli

    result = CliRunner().invoke(
        cli,
        [
            "init-consumer",
            "--target-dir",
            str(tmp_path),
            "--profile",
            "olang",
            "--with-sandbox",
            "--with-enforcement",
            "--governed-mode",
            "strict",
        ],
    )
    assert result.exit_code == 0, result.output

    config = json.loads(
        (tmp_path / ".apatch" / "sandbox.json").read_text(encoding="utf-8")
    )
    for pattern in ("o_lang/**", "docs/RFP-*.md", "docs/specs/**", "AGENTS.md"):
        assert pattern in config["protected_globs"]
    assert "docs/*.md" not in config["allow_globs"]
    assert "*.md" not in config["allow_globs"]

    real_open = open
    handles = []

    def tracked_open(*args, **kwargs):
        handle = real_open(*args, **kwargs)
        handles.append(handle)
        return handle

    monkeypatch.setattr("builtins.open", tracked_open)
    assert load_sandbox_config(str(tmp_path))["mode"] == "enforce"
    assert handles
    assert all(handle.closed for handle in handles)

    for path in (
        "o_lang/cpp/include/frontend/example.hpp",
        "docs/RFP-340-olang-tower-user-api.md",
        "docs/specs/SPEC-OLANG-TOWER-USER-API-1.md",
        "AGENTS.md",
    ):
        decision = evaluate_write_policy(
            str(tmp_path), path, cfg=config, channel="agent_hook"
        )
        assert decision["allowed"] is False, (path, decision)
        assert decision["reason"] == "direct_write_blocked"

    assert evaluate_write_policy(
        str(tmp_path), "docs/README.md", cfg=config, channel="agent_hook"
    )["allowed"] is True


def test_init_consumer_copies_profile_doc(tmp_path):
    created = init_consumer(str(tmp_path), profile="sqlalchemy")
    profile_md = tmp_path / "manifests" / "PROFILE.sqlalchemy.md"
    assert profile_md.exists()
    text = profile_md.read_text(encoding="utf-8")
    assert "SQLAlchemy" in text or "Alembic" in text
    assert "docs/profiles" not in text
    assert any("PROFILE.sqlalchemy.md" in p for p in created) or profile_md.exists()


def test_init_consumer_with_arch_rules(tmp_path):
    created = init_consumer(str(tmp_path), with_arch_rules=True)
    assert (tmp_path / "manifests" / "arch-rules.yaml").exists()
    assert (tmp_path / "manifests" / "semantic-verify.yaml").exists()
    assert (tmp_path / "manifests" / "engineering-pipeline.example.json").exists()
    assert any("arch-rules.yaml" in p for p in created)


def test_init_consumer_merges_npm_scripts(tmp_path):
    pkg = tmp_path / "package.json"
    pkg.write_text('{"name": "app", "scripts": {"build": "vite build"}}', encoding="utf-8")
    created = init_consumer(str(tmp_path))
    assert str(pkg) in created
    import json

    data = json.loads(pkg.read_text(encoding="utf-8"))
    assert "apatch:doctor" in data["scripts"]
    assert "apatch:strip-dry" in data["scripts"]
