"""``apatch conformance`` — opt-in standing contract-gate CLI (RFP-035).

Two terminal commands, each with ``--json`` for agents/CI:

* ``apatch conformance status`` — classify the contract (conformant / drifted /
  stale / unproven), no live verify by default (fast, from attestation state);
* ``apatch conformance gate`` — the gate: red only on a real ``drifted`` (live
  verify red). ``--live`` runs verifies; ``--base REF`` makes it baseline-aware
  (only re-run specs whose files changed since REF); ``--blocking`` overrides the
  configured mode and exits non-zero on a red contract.
"""

import json

import click

from apatch.conformance import conformance_gate, conformance_status


def _print_identity(out):
    workspace = out.get("workspace")
    if workspace:
        click.echo(
            "workspace: {} | cwd: {} | config: {}".format(
                workspace,
                out.get("cwd") or "?",
                out.get("config_path") or "?",
            )
        )


def _print_verify_details(out):
    for spec in out.get("per_spec") or []:
        for detail in spec.get("verify_details") or []:
            click.echo(f"  {spec.get('spec')}#{detail.get('id')} {detail.get('kind')} exit={detail.get('exit_code')}")
            click.echo(f"    cmd: {detail.get('cmd')}")
            stdout = (detail.get("stdout_tail") or "").strip()
            stderr = (detail.get("stderr_tail") or "").strip()
            if stdout:
                click.echo("    stdout tail:")
                click.echo("\n".join("      " + line for line in stdout.splitlines()[-20:]))
            if stderr:
                click.echo("    stderr tail:")
                click.echo("\n".join("      " + line for line in stderr.splitlines()[-20:]))


@click.group("conformance")
def conformance_group():
    """Continuous conformance — does the whole contract still hold (RFP-035)."""


@conformance_group.command("status")
@click.option("--live", is_flag=True, help="Run each spec's verify now (slower, truthful).")
@click.option("--no-live", is_flag=True, help="Do not run verifies, even when conformance.json has live_verify=true.")
@click.option("--base", default=None, help="Baseline ref — only re-verify specs changed since it.")
@click.option("--spec", "specs", multiple=True, help="Limit the contract run to one SPEC id; repeatable.")
@click.option("--timeout", "timeout_sec", default=None, type=int, help="Per-requirement live verify timeout seconds.")
@click.option("--spec-budget-sec", default=None, type=int, help="Maximum live verify time budget per spec.")
@click.option("--jobs", default=None, type=int, help="Parallel live spec workers.")
@click.option("--requirement-jobs", default=None, type=int, help="Parallel requirement workers inside each live spec.")
@click.option("--exhaustive", is_flag=True, help="Run every requirement even after the first live failure/broken verify.")
@click.option("--target-dir", default=".")
@click.option("--json", "as_json", is_flag=True)
def conformance_status_cmd(live, no_live, base, specs, timeout_sec, spec_budget_sec, jobs, requirement_jobs, exhaustive, target_dir, as_json):
    """Per-spec conformance over the contract, all buckets at once."""
    out = conformance_status(
        target_dir,
        specs=list(specs) or None,
        live=live,
        no_live=no_live,
        base=base,
        timeout=timeout_sec,
        spec_budget_sec=spec_budget_sec,
        jobs=jobs,
        requirement_jobs=requirement_jobs,
        fail_fast=not exhaustive,
    )
    if as_json:
        click.echo(json.dumps(out, ensure_ascii=False, indent=2))
        return
    _print_identity(out)
    if not out.get("enabled"):
        click.echo(f"conformance: disabled — {out.get('skipped')}")
        return
    b = out["buckets"]
    click.echo(
        f"contract: {out['gated']} specs | conformant={b['conformant']} "
        f"drifted={b['drifted']} stale={b['stale']} unproven={b['unproven']}"
        + (f" | live-verified={out['verified_live']}" if out.get("live") else "")
    )
    if out["drifted"]:
        click.echo("  drifted (red verify): " + ", ".join(out["drifted"]))
        _print_verify_details(out)
    if out["stale"]:
        click.echo("  stale (re-attest): " + ", ".join(out["stale"]))
        for spec in out.get("per_spec") or []:
            if spec.get("conformance") == "stale" and spec.get("recommended_action"):
                click.echo(f"    {spec.get('spec')}: {spec.get('recommended_action')}")
                if spec.get("recommended_cli"):
                    click.echo(f"      cli: {spec.get('recommended_cli')}")
    if out["unproven"]:
        click.echo("  unproven (no verify): " + ", ".join(out["unproven"]))


@conformance_group.command("gate")
@click.option("--live", is_flag=True, help="Run each spec's verify now (the truthful gate).")
@click.option("--no-live", is_flag=True, help="Do not run verifies, even when conformance.json has live_verify=true.")
@click.option("--base", default=None, help="Baseline ref — only re-verify specs changed since it (A35-H).")
@click.option("--spec", "specs", multiple=True, help="Limit the contract run to one SPEC id; repeatable.")
@click.option("--blocking", is_flag=True, help="Exit non-zero on a red (drifted) contract.")
@click.option("--ci-safe", "ci_safe", is_flag=True, help="Skip env-dependent verifies (sibling repo / ledger / shell chains) — block only on self-contained pytest, safe in CI.")
@click.option("--timeout", "timeout_sec", default=None, type=int, help="Per-requirement live verify timeout seconds.")
@click.option("--spec-budget-sec", default=None, type=int, help="Maximum live verify time budget per spec.")
@click.option("--jobs", default=None, type=int, help="Parallel live spec workers.")
@click.option("--requirement-jobs", default=None, type=int, help="Parallel requirement workers inside each live spec.")
@click.option("--exhaustive", is_flag=True, help="Run every requirement even after the first live failure/broken verify.")
@click.option("--target-dir", default=".")
@click.option("--json", "as_json", is_flag=True)
def conformance_gate_cmd(live, no_live, base, specs, ci_safe, blocking, timeout_sec, spec_budget_sec, jobs, requirement_jobs, exhaustive, target_dir, as_json):
    """Standing contract-gate: red only on a real drifted (live red verify)."""
    out = conformance_gate(
        target_dir,
        live=live,
        no_live=no_live,
        base=base,
        ci_safe=ci_safe,
        specs=list(specs) or None,
        timeout=timeout_sec,
        spec_budget_sec=spec_budget_sec,
        jobs=jobs,
        requirement_jobs=requirement_jobs,
        fail_fast=not exhaustive,
    )
    if as_json:
        click.echo(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        _print_identity(out)
        gate = out.get("gate")
        if gate == "skipped":
            click.echo(f"conformance gate: skipped — {out.get('skipped')}")
        elif gate == "passed":
            adv = out.get("advisories") or {}
            extra = f" ({adv.get('stale', 0)} stale, {adv.get('unproven', 0)} unproven advisories)" if adv else ""
            click.echo(f"conformance gate: passed{extra}")
        else:
            click.echo(f"conformance gate: {gate} — {out.get('reason')}")
            click.echo("  drifted: " + ", ".join(out.get("drifted") or []))
            _print_verify_details(out)
    # --blocking forces a non-zero exit on a red contract regardless of config mode.
    if blocking and not out.get("contract_holds", True):
        raise SystemExit(1)
