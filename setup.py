"""Bundle canonical consumer templates without a second maintained source copy."""

from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py

CONSUMER_ASSETS = (
    "docs/AGENTS.template.md",
    "docs/ci/github-action-apatch.yml",
    "docs/devcontainer/devcontainer.json",
    "docs/manifests/arch-rules.example.yaml",
    "docs/manifests/semantic-verify.example.yaml",
    "docs/manifests/engineering-pipeline.example.json",
    "docs/cookbook.md",
    "docs/design-partner-playbook.md",
    "docs/profiles/frontend.md",
    "docs/profiles/sqlalchemy-alembic.md",
    "docs/profiles/elasticsearch-opensearch.md",
    "docs/profiles/prisma.md",
    "docs/profiles/django.md",
    "docs/profiles/cosmos-sdk.md",
    "scripts/ci/apatch-sandbox-gate.sh",
    "scripts/hooks/pre-commit-trustchain.sh",
    "scripts/cursor-hooks/apatch-deny-direct-edit.sh",
    "scripts/cursor-hooks/apatch-deny-shell-mutate.sh",
    "scripts/cursor-hooks/apatch-deny-mcp-mutate.sh",
    "scripts/cursor-hooks/hooks.json",
)


class BuildPyWithConsumerAssets(build_py):
    def get_source_files(self):
        # egg_info/sdist must retain the original assets so wheel-from-sdist
        # builds have exactly the same inputs as wheel-from-checkout builds.
        return super().get_source_files() + list(CONSUMER_ASSETS)

    def run(self):
        super().run()
        for relative in CONSUMER_ASSETS:
            source = Path(relative)
            if not source.is_file():
                raise FileNotFoundError(
                    f"Missing required APatch consumer resource: {relative}"
                )
            destination = Path(self.build_lib) / "apatch/_consumer_assets" / relative
            self.mkpath(str(destination.parent))
            self.copy_file(str(source), str(destination))

    def get_outputs(self, include_bytecode=True):
        assets = [
            str(Path(self.build_lib) / "apatch/_consumer_assets" / relative)
            for relative in CONSUMER_ASSETS
        ]
        return super().get_outputs(include_bytecode=include_bytecode) + assets


setup(cmdclass={"build_py": BuildPyWithConsumerAssets})
