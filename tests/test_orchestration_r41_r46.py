"""Tests for orchestration tools R41–R46."""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from apatch.workflows import (
    arch_check_workspace,
    db_check_workspace,
    db_revision_workspace,
    db_safety_workspace,
    impact_analysis,
)

ARCH_RULES = """
version: 1
forbidden_imports:
  - from: "sales/**"
    to: "auth/**"
layer_rules:
  - layer: frontend
    paths: ["frontend/**"]
    may_import: ["shared/**"]
  - layer: backend
    paths: ["backend/**"]
  - rule: "frontend cannot import backend"
"""


def _git_init(root: Path) -> None:
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=root, check=True)


def _git_commit(root: Path, message: str = "init") -> None:
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", message], cwd=root, check=True, capture_output=True)


@pytest.fixture
def git_project(tmp_path):
    _git_init(tmp_path)
    yield tmp_path


def test_arch_check_layer_violation(git_project, tmp_path):
    pytest.importorskip("yaml")
    manifests = git_project / "manifests"
    manifests.mkdir()
    (manifests / "arch-rules.yaml").write_text(ARCH_RULES, encoding="utf-8")
    (git_project / "frontend").mkdir()
    (git_project / "backend").mkdir()
    (git_project / "backend" / "service.py").write_text("X = 1\n", encoding="utf-8")
    (git_project / "frontend" / "page.py").write_text(
        "from backend.service import X\n", encoding="utf-8"
    )
    _git_commit(git_project)

    result = arch_check_workspace(str(git_project))
    assert result["ok"] is False
    types = {v["type"] for v in result["violations"]}
    assert "layer_violation" in types


def test_arch_check_forbidden_import(git_project):
    pytest.importorskip("yaml")
    manifests = git_project / "manifests"
    manifests.mkdir()
    rules = {
        "version": 1,
        "forbidden_imports": [{"from": "sales/**", "to": "auth/**"}],
        "layer_rules": [],
        "service_rules": [],
    }
    (manifests / "arch-rules.yaml").write_text(yaml.dump(rules), encoding="utf-8")
    (git_project / "sales").mkdir(parents=True)
    (git_project / "auth").mkdir(parents=True)
    (git_project / "auth" / "models.py").write_text("class A: pass\n", encoding="utf-8")
    (git_project / "sales" / "service.py").write_text(
        "from auth.models import A\n", encoding="utf-8"
    )
    _git_commit(git_project)

    result = arch_check_workspace(str(git_project))
    assert result["ok"] is False
    assert any(v["type"] == "forbidden_import" for v in result["violations"])


def test_impact_file_and_symbol(git_project):
    (git_project / "backend").mkdir()
    (git_project / "api").mkdir()
    (git_project / "tests").mkdir()
    (git_project / "backend" / "models.py").write_text("class UserModel: pass\n", encoding="utf-8")
    (git_project / "api" / "users.py").write_text(
        "from backend.models import UserModel\n\ndef get(): return UserModel\n",
        encoding="utf-8",
    )
    (git_project / "tests" / "test_users.py").write_text(
        "from api.users import get\n", encoding="utf-8"
    )

    by_file = impact_analysis("backend/models.py", str(git_project), depth=2)
    assert "api/users.py" in by_file["affected_files"]

    by_sym = impact_analysis("UserModel", str(git_project), kind="symbol", depth=2)
    assert by_sym["defined_in"] == "backend/models.py"
    assert "api/users.py" in by_sym["affected_files"] or "tests/test_users.py" in by_sym["affected_files"]


def test_db_check_missing_migration(git_project):
    (git_project / "app").mkdir()
    (git_project / "alembic" / "versions").mkdir(parents=True)
    (git_project / "app" / "db_models.py").write_text("class User: pass\n", encoding="utf-8")
    _git_commit(git_project)

    (git_project / "app" / "db_models.py").write_text(
        "class User: pass\nclass Order: pass\n", encoding="utf-8"
    )

    result = db_check_workspace(str(git_project), profile="sqlalchemy")
    assert result["ok"] is False
    assert result["reason"] == "missing_migration"
    assert "app/db_models.py" in result["changed_model_files"]


def test_db_check_ok_with_migration(git_project):
    (git_project / "app").mkdir()
    versions = git_project / "alembic" / "versions"
    versions.mkdir(parents=True)
    (git_project / "app" / "db_models.py").write_text("class User: pass\n", encoding="utf-8")
    _git_commit(git_project)

    (git_project / "app" / "db_models.py").write_text(
        "class User: pass\nclass Order: pass\n", encoding="utf-8"
    )
    (versions / "001_add_order.py").write_text("# migration\n", encoding="utf-8")

    result = db_check_workspace(str(git_project), profile="sqlalchemy")
    assert result["ok"] is True
    assert result["models_changed"] is True


def test_db_revision_dry_run(git_project):
    result = db_revision_workspace(
        str(git_project), profile="sqlalchemy", message="test", dry_run=True
    )
    assert result["ok"] is True
    assert "alembic" in result["command_run"]
    assert result["dry_run"] is True


def test_db_safety_detects_drop_column(git_project):
    versions = git_project / "alembic" / "versions"
    versions.mkdir(parents=True)
    (versions / "bad.py").write_text(
        "def upgrade():\n    op.drop_column('users', 'legacy_id')\n",
        encoding="utf-8",
    )
    _git_commit(git_project)

    result = db_safety_workspace(str(git_project), profile="sqlalchemy", since=None)
    assert result["safe"] is False
    assert result["risk"] == "high"
    assert any(f["type"] == "drop_column" for f in result["findings"])


def test_cli_arch_check_json(git_project, tmp_path):
    pytest.importorskip("yaml")
    from click.testing import CliRunner

    from apatch.cli import cli

    manifests = git_project / "manifests"
    manifests.mkdir()
    shutil.copy(
        Path(__file__).resolve().parents[1] / "docs/manifests/arch-rules.example.yaml",
        manifests / "arch-rules.yaml",
    )
    (git_project / "frontend").mkdir()
    (git_project / "backend").mkdir()
    (git_project / "backend" / "service.py").write_text("X=1\n", encoding="utf-8")
    (git_project / "frontend" / "Sales.tsx").write_text(
        "import { x } from '../backend/service'\n", encoding="utf-8"
    )
    _git_commit(git_project)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["arch", "check", "--target-dir", str(git_project), "--json"],
    )
    assert result.exit_code == 1
    import json

    data = json.loads(result.output)
    assert data["ok"] is False
