"""Entry point for ``apatch-mcp`` — activates stdio guard before any apatch imports."""

from __future__ import annotations

import os
import sys
from importlib import metadata

from apatch.warn_filters import configure_apatch_warnings

configure_apatch_warnings()

from apatch.mcp.stdio_guard import activate_stdio_guard  # noqa: E402


def main() -> None:
    bound = os.environ.get("APATCH_MCP_BOUND")
    activate_stdio_guard(workspace=bound)
    if os.environ.get("APATCH_CANONICAL_RUNTIME") == "1":
        from apatch import __version__

        try:
            installed = metadata.version("apatch")
        except metadata.PackageNotFoundError:
            installed = None
        if installed and installed != __version__:
            sys.stderr.write(
                "apatch MCP runtime mismatch: loaded {} but installed {}; "
                "restart from the canonical .apatch/mcp.json interpreter.\n".format(
                    __version__, installed
                )
            )
            raise SystemExit(78)
    from apatch.mcp.bound_workspace import bind_mcp_workspace

    bind_mcp_workspace(bound)
    from apatch.mcp.lifecycle import on_mcp_startup
    from apatch.mcp.server import run_mcp_stdio

    on_mcp_startup()
    run_mcp_stdio()


if __name__ == "__main__":
    main()
