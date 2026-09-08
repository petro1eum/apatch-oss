"""Detached async verify worker.

The MCP process only registers and launches this worker. The worker owns the verify
subprocess, drains stdout/stderr continuously via communicate(), and persists the
terminal job record before it exits, so MCP restarts cannot orphan a running verify.
"""

from __future__ import annotations

import argparse

from apatch.verify_jobs import run_verify_job_worker


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--job-id", required=True)
    args = parser.parse_args()
    raise SystemExit(run_verify_job_worker(args.root, args.job_id))


if __name__ == "__main__":
    main()
