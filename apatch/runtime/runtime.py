"""MutationRuntime — single facade for plan / apply / rollback (RFP-004 E1)."""

from __future__ import annotations

import os
import secrets
from typing import Any, Dict, List, Optional

from apatch.runtime.errors import (
    HygieneCriticalError,
    RuntimeTransitionError,
    SessionBindingError,
)
from apatch.apply_session import run_apply_session
from apatch.runtime.state_machine import (
    OP_APPLY,
    OP_APPLY_SESSION,
    OP_ATTEST,
    OP_PIPELINE,
    OP_PLAN,
    OP_REPLAY,
    OP_ROLLBACK,
    OP_STRIP,
    OP_VERIFY,
    assert_operation,
)
from apatch.session_state import enrich_tool_response
from apatch.workflows import (
    WorkflowError,
    apply_from_logs,
    phase_run as workflow_phase_run,
    pipeline_run_manifest,
    plan_from_logs,
    replay_session_workspace,
    rollback_workspace,
    run_strip,
)


def _spec_requirement_attest_warning(
    trustchain: Any,
    *,
    artifacts: Any,
    session_id: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Warn when normal attest cannot close an executable-spec requirement.

    SPEC status is ledger-derived: a requirement is attested only when the
    artifact has an attestation plus either a mutation or explicit covered_by.
    A plain verify+attest session is useful audit data, but it leaves the Rk
    pending. Surface that before agents mistake a committed attestation for a
    closed requirement.
    """

    spec_artifacts: List[Dict[str, Any]] = []
    for art in artifacts or []:
        if not isinstance(art, dict):
            continue
        if art.get("kind") == "spec" and "#" in str(art.get("id") or ""):
            spec_artifacts.append(art)
    if not spec_artifacts:
        return None

    try:
        from apatch.traceability import build_traceability_index

        entries = list(trustchain.iter_ledger_entries())
        by_artifact = build_traceability_index(entries).get("by_artifact") or {}
    except Exception:
        return None

    missing: List[str] = []
    for art in spec_artifacts:
        key = "{}:{}".format(art.get("kind"), art.get("id"))
        bucket = by_artifact.get(key) or {}
        mutations = list(bucket.get("mutations") or [])
        if session_id:
            mutations = [
                m
                for m in mutations
                if str(m.get("governed_session_id") or "") == str(session_id)
            ]
        if not mutations:
            missing.append(key)

    if not missing:
        return None
    return {
        "code": "SPEC_ATTEST_WITHOUT_MUTATION",
        "message": (
            "This attestation is linked to executable-spec requirement(s), but "
            "the current session has no mutation for them. spec_status will keep "
            "the requirement pending unless you use a real mutation or "
            "apatch_noop_attest(covered_by=...)."
        ),
        "artifacts": missing,
        "will_close_spec_requirement": False,
        "recommended_action": (
            "Bind a real mutation to this Rk, or use apatch_noop_attest with a "
            "non-empty covered_by list when another requirement's mutation truly "
            "satisfies it."
        ),
    }


class MutationRuntime:
    """Session-oriented mutation facade over existing workflows."""

    def __init__(
        self,
        target_dir: str = ".",
        *,
        session_id: Optional[str] = None,
        session_token: Optional[str] = None,
        enforce_binding: bool = False,
    ):
        self.target_dir = os.path.abspath(target_dir)
        self.session_id = session_id
        self.session_token = session_token
        self.enforce_binding = enforce_binding
        self._expected_revision: Optional[int] = None

    def _capture_binding(self, operation: str):
        from apatch.runtime.session_binding import capture_session_binding

        binding = capture_session_binding(
            self.target_dir,
            expected_session_id=self.session_id,
            session_token=self.session_token,
            require_capability=self.enforce_binding,
            operation=operation,
        )
        if binding is not None:
            self.session_id = binding.session_id
            self._expected_revision = binding.revision
        return binding

    def _finish(self, tool_name: str, result: Dict[str, Any]) -> Dict[str, Any]:
        out = enrich_tool_response(
            tool_name,
            result,
            target_dir=self.target_dir,
            expected_session_id=self.session_id,
            expected_revision=self._expected_revision,
        )
        if self.session_id and out.get("error_type") not in (
            "SESSION_MISMATCH",
            "SESSION_REVISION_MISMATCH",
        ):
            from apatch.session_state import load_session_state

            raw = load_session_state(self.target_dir)
            if str(raw.get("session_id") or "") == self.session_id:
                self._expected_revision = int(raw.get("revision") or 0)
        return out

    def _ensure_mutation(
        self,
        operation: str,
        *,
        intent_hint: Optional[str] = None,
        skip: bool = False,
    ) -> None:
        if skip:
            return
        from apatch.artifact_governance import (
            assert_governed_hygiene_allowed,
            increment_governed_ops_counter,
        )
        from apatch.runtime.session import ensure_governed_session

        assert_governed_hygiene_allowed(self.target_dir)
        increment_governed_ops_counter(self.target_dir)
        ensure_governed_session(
            self.target_dir,
            operation=operation,
            intent_hint=intent_hint,
        )
        from apatch.sdd_integrity import admit_session_effect

        admit_session_effect(
            self.target_dir,
            {
                "surface": "mcp_tool",
                "effect": "invoke",
                "tool": operation if operation.startswith("apatch_") else f"apatch_{operation}",
            },
        )

    def plan(
        self,
        logs_path: str,
        *,
        tool: Optional[str] = None,
        keyword: Optional[str] = None,
        steps: Optional[str] = None,
        range_str: Optional[str] = None,
        show_diff: bool = False,
        workers: int = 0,
        skip_transition_check: bool = False,
    ) -> Dict[str, Any]:
        try:
            if not skip_transition_check:
                self._ensure_mutation("plan", intent_hint="auto: plan via MCP")
                assert_operation(self.target_dir, OP_PLAN)
            result = plan_from_logs(
                logs_path,
                self.target_dir,
                tool=tool,
                keyword=keyword,
                steps=steps,
                range_str=range_str,
                show_diff=show_diff,
                workers=workers,
            )
            result = {**result, "ok": True}
            return self._finish("apatch_plan_batch", result)
        except RuntimeTransitionError as e:
            return e.to_dict()
        except WorkflowError as e:
            return {"ok": False, "error": str(e), "entries": []}

    def apply_interactive(
        self,
        logs_path: str,
        *,
        tool: Optional[str] = None,
        keyword: Optional[str] = None,
        steps: Optional[str] = None,
        range_str: Optional[str] = None,
        replace_all: bool = False,
        min_confidence: Optional[float] = None,
        only_drifted: bool = False,
        verify: Optional[str] = None,
        verify_deferred: bool = False,
        no_trustchain: bool = False,
        dry_run: bool = False,
        report_path: Optional[str] = None,
        skip_transition_check: bool = False,
    ) -> Dict[str, Any]:
        try:
            if not dry_run and not skip_transition_check:
                self._ensure_mutation("apply", intent_hint="auto: apply via MCP")
                binding = self._capture_binding("apply")
                if binding is not None:
                    from apatch.runtime.session_binding import (
                        patch_log_contract_exists,
                        validate_patch_log_contract,
                    )

                    if self.enforce_binding or patch_log_contract_exists(
                        self.target_dir, logs_path
                    ):
                        validate_patch_log_contract(
                            self.target_dir,
                            logs_path,
                            expected_session_id=binding.session_id,
                        )
                assert_operation(self.target_dir, OP_APPLY)
                self._capture_binding("apply")
            result = apply_from_logs(
                logs_path,
                self.target_dir,
                tool=tool,
                keyword=keyword,
                steps=steps,
                range_str=range_str,
                replace_all=replace_all,
                min_confidence=min_confidence,
                only_drifted=only_drifted,
                verify=verify,
                verify_deferred=verify_deferred,
                no_trustchain=no_trustchain,
                dry_run=dry_run,
                report_path=report_path,
                interactive=True,
                quiet=False,
            )
            return self._finish("apatch_apply", result)
        except (RuntimeTransitionError, SessionBindingError) as e:
            return e.to_dict()
        except WorkflowError as e:
            return {"ok": False, "error": str(e), "applied": 0, "failed": 0, "skipped": 0}

    def apply(
        self,
        logs_path: str,
        *,
        tool: Optional[str] = None,
        keyword: Optional[str] = None,
        steps: Optional[str] = None,
        range_str: Optional[str] = None,
        replace_all: bool = False,
        min_confidence: Optional[float] = None,
        only_drifted: bool = False,
        verify: Optional[str] = None,
        verify_deferred: bool = False,
        no_trustchain: bool = False,
        dry_run: bool = False,
        report_path: Optional[str] = None,
        change_budget: Optional[Dict[str, int]] = None,
        quiet: bool = False,
        skip_transition_check: bool = False,
    ) -> Dict[str, Any]:
        try:
            if not dry_run and not skip_transition_check:
                self._ensure_mutation("apply", intent_hint="auto: apply via MCP")
                binding = self._capture_binding("apply")
                if binding is not None:
                    from apatch.runtime.session_binding import (
                        patch_log_contract_exists,
                        validate_patch_log_contract,
                    )

                    if self.enforce_binding or patch_log_contract_exists(
                        self.target_dir, logs_path
                    ):
                        validate_patch_log_contract(
                            self.target_dir,
                            logs_path,
                            expected_session_id=binding.session_id,
                        )
                assert_operation(self.target_dir, OP_APPLY)
                self._capture_binding("apply")
            result = apply_from_logs(
                logs_path,
                self.target_dir,
                tool=tool,
                keyword=keyword,
                steps=steps,
                range_str=range_str,
                replace_all=replace_all,
                min_confidence=min_confidence,
                only_drifted=only_drifted,
                verify=verify,
                verify_deferred=verify_deferred,
                no_trustchain=no_trustchain,
                dry_run=dry_run,
                report_path=report_path,
                change_budget=change_budget,
                quiet=quiet,
            )
            return self._finish("apatch_apply", result)
        except (RuntimeTransitionError, SessionBindingError) as e:
            return e.to_dict()
        except WorkflowError as e:
            return {"ok": False, "error": str(e), "applied": 0, "failed": 0, "skipped": 0}

    def rollback(
        self,
        session_id: Optional[str] = None,
        *,
        preview: bool = False,
    ) -> Dict[str, Any]:
        acquired_lease: Optional[Dict[str, Any]] = None
        release_acquired = False
        try:
            binding = None
            if self.enforce_binding or self.session_id or not preview:
                binding = self._capture_binding("rollback")
            if not preview:
                assert_operation(self.target_dir, OP_ROLLBACK)
            rollback_session = session_id
            rollback_paths: List[str] = []
            if binding is not None:
                from apatch.runtime.session_binding import resolve_governed_rollback

                resolved = resolve_governed_rollback(
                    self.target_dir,
                    binding,
                    requested_checkpoint=session_id,
                )
                rollback_session = str(resolved["checkpoint"])
                rollback_paths = list(resolved.get("paths") or [])
            if binding is not None and rollback_paths and not preview:
                from apatch.sandbox import acquire_lease, load_active_lease

                existing = load_active_lease(
                    self.target_dir,
                    governed_session_id=binding.session_id,
                )
                acquired_lease = acquire_lease(
                    self.target_dir,
                    rollback_paths,
                    tool="apatch_rollback",
                    governed_session_id=binding.session_id,
                    session_checkpoint=rollback_session,
                )
                release_acquired = existing is None
            result = rollback_workspace(
                self.target_dir,
                session_id=rollback_session,
                preview=preview,
                governed_session_id=(binding.session_id if binding is not None else None),
            )
            if binding is not None:
                result.setdefault("governed_session_id", binding.session_id)
                result.setdefault("checkpoint", rollback_session)
            return self._finish("apatch_rollback", result)
        except (RuntimeTransitionError, SessionBindingError) as e:
            return e.to_dict()
        except Exception as e:
            from apatch.sandbox import SandboxError

            if isinstance(e, SandboxError):
                return {
                    "ok": False,
                    "error": str(e),
                    "error_type": e.error_type,
                    "details": e.details,
                    "mutation_performed": False,
                    "recoverable": True,
                    "recommended_action": "retry_after_conflicting_session_finishes",
                }
            raise
        finally:
            if acquired_lease is not None and release_acquired:
                try:
                    from apatch.sandbox import release_lease

                    release_lease(
                        self.target_dir,
                        lease_id=acquired_lease.get("lease_id"),
                        governed_session_id=self.session_id,
                    )
                except Exception:
                    pass

    def resume_session(self) -> Dict[str, Any]:
        """Rotate the capability for one exact active governed session.

        With multiple active lanes the caller must provide ``session_id`` to the
        runtime (MCP: ``governed_session_id``). The id selects only its registered
        lane; the plaintext recovery token is returned once and never persisted.
        """
        from apatch.session_state import load_session_state, save_session_state
        from apatch.runtime.errors import SessionBindingError
        from apatch.runtime.session_binding import hash_session_token
        from apatch.runtime.state_machine import current_lifecycle

        try:
            binding = self._capture_binding("apatch_resume_session")
        except SessionBindingError as exc:
            return exc.to_dict()
        if binding is None:
            return {
                "ok": False,
                "resumed": False,
                "error": "no active session to resume",
                "error_type": "RUNTIME_TRANSITION",
                "recommended_action": "apatch_session_start",
            }

        raw = load_session_state(self.target_dir)
        if (
            str(raw.get("session_id") or "") != binding.session_id
            or raw.get("ended_at")
        ):
            return SessionBindingError(
                "The requested governed session is not active.",
                error_type="SESSION_MISMATCH",
                expected=binding.session_id,
                actual=str(raw.get("session_id") or "") or None,
            ).to_dict()

        before = current_lifecycle(self.target_dir)
        failure = raw.get("failure") if isinstance(raw.get("failure"), dict) else {}
        details = failure.get("details") if isinstance(failure.get("details"), dict) else {}
        reapply = bool(
            failure.get("error_type") == "VERIFY_FAILED"
            and details.get("rollback_performed") is True
        )
        resume_mode = "reapply" if reapply else "verify"
        session_token = secrets.token_urlsafe(32)
        raw["failure"] = None
        raw["phase"] = "apply" if reapply else "verify"
        raw["session_capability_version"] = 1
        raw["session_token_hash"] = hash_session_token(session_token)
        try:
            save_session_state(
                self.target_dir,
                raw,
                force=True,
                expected_session_id=binding.session_id,
                expected_revision=binding.revision,
            )
        except SessionBindingError as exc:
            return exc.to_dict()
        self.session_id = binding.session_id
        self.session_token = session_token
        self._expected_revision = int(raw.get("revision") or 0)
        after = current_lifecycle(self.target_dir)
        return {
            "ok": True,
            "resumed": True,
            "from_lifecycle": before,
            "lifecycle": after,
            "session_id": binding.session_id,
            "session_token": session_token,
            "session_capability": {
                "session_id": binding.session_id,
                "session_token": session_token,
            },
            "resume_mode": resume_mode,
            "next_action": (
                "apatch_generate_batch(corrected needles), then "
                "apatch_apply_session(reset=true, ...)"
                if reapply
                else "apatch_verify_run() then apatch_attest()"
            ),
        }

    def recover_session(self) -> Dict[str, Any]:
        """Recover one exact governed session in one idempotent operation."""
        from apatch.runtime.recovery import recover_session

        return recover_session(self.target_dir, str(self.session_id or ""))

    def _reset_phase_for_reapply(
        self,
        *,
        session_path: Optional[str] = None,
    ) -> None:
        """Make an intentional reset legal before reconciliation runs.

        A completed apply-session artifact can make reconciliation restore the
        ``verifying`` lifecycle immediately after the phase is moved to ``apply``.
        Discard that stale chunk cursor first; the already-applied source and its
        checkpoint remain intact for fix-forward or rollback.
        """
        from apatch.apply_session import discard_apply_session_state
        from apatch.session_state import load_session_state, save_session_state
        from apatch.runtime.state_machine import current_lifecycle
        from apatch.runtime.domain import LIFECYCLE_COMMITTED, LIFECYCLE_VERIFYING

        raw = load_session_state(self.target_dir)
        if not raw.get("session_id") or raw.get("ended_at"):
            return
        expected_session_id = str(raw.get("session_id") or self.session_id or "")
        discard_apply_session_state(
            self.target_dir,
            session_path,
            expected_session_id=expected_session_id,
        )
        if session_path is not None:
            discard_apply_session_state(
                self.target_dir,
                expected_session_id=expected_session_id,
            )
        if current_lifecycle(self.target_dir) in (LIFECYCLE_VERIFYING, LIFECYCLE_COMMITTED):
            raw["phase"] = "apply"
            raw["failure"] = None
            save_session_state(self.target_dir, raw, force=True)

    def apply_session(
        self,
        logs_path: str,
        *,
        session_path: Optional[str] = None,
        verify: Optional[str] = None,
        chunk_max_files: int = 5,
        replace_all: bool = False,
        only_drifted: bool = False,
        min_confidence: Optional[float] = None,
        verify_deferred: bool = True,
        no_trustchain: bool = False,
        tool: Optional[str] = None,
        keyword: Optional[str] = None,
        reset: bool = False,
        abort: bool = False,
        quiet: bool = False,
    ) -> Dict[str, Any]:
        try:
            if not abort:
                binding = self._capture_binding("apply_session")
                if reset:
                    if session_path is None:
                        self._reset_phase_for_reapply()
                    else:
                        self._reset_phase_for_reapply(session_path=session_path)
                self._ensure_mutation(
                    "apply_session",
                    intent_hint="auto: apply_session via MCP",
                )
                if binding is not None:
                    from apatch.runtime.session_binding import (
                        patch_log_contract_exists,
                        validate_patch_log_contract,
                    )

                    if self.enforce_binding or patch_log_contract_exists(
                        self.target_dir, logs_path
                    ):
                        validate_patch_log_contract(
                            self.target_dir,
                            logs_path,
                            expected_session_id=binding.session_id,
                        )
                assert_operation(self.target_dir, OP_APPLY_SESSION)
                self._capture_binding("apply_session")
            result = run_apply_session(
                logs_path,
                self.target_dir,
                session_path=session_path,
                verify=verify,
                chunk_max_files=chunk_max_files,
                replace_all=replace_all,
                only_drifted=only_drifted,
                min_confidence=min_confidence,
                verify_deferred=verify_deferred,
                no_trustchain=no_trustchain,
                tool=tool,
                keyword=keyword,
                reset=reset,
                abort=abort,
                quiet=quiet,
            )
            return self._finish("apatch_apply_session", result)
        except HygieneCriticalError as e:
            return e.to_dict()
        except (RuntimeTransitionError, SessionBindingError) as e:
            return e.to_dict()
        except WorkflowError as e:
            return {"ok": False, "error": str(e), "continue": False}

    def verify_semantic(
        self,
        *,
        rules_path: Optional[str] = None,
        since: str = "HEAD",
        skip_transition_check: bool = False,
    ) -> Dict[str, Any]:
        from apatch.workflows import semantic_verify_workspace

        try:
            if not skip_transition_check:
                assert_operation(self.target_dir, OP_VERIFY)
            result = semantic_verify_workspace(
                self.target_dir, rules_path=rules_path, since=since
            )
            return self._finish("apatch_verify_semantic", result)
        except RuntimeTransitionError as e:
            return e.to_dict()

    def strip(self, file_path: str, **kwargs: Any) -> Dict[str, Any]:
        try:
            self._ensure_mutation("strip", intent_hint="auto: strip via MCP")
            assert_operation(self.target_dir, OP_STRIP)
            result = run_strip(file_path, **kwargs)
            if isinstance(result, dict) and "ok" not in result:
                result = {**result, "ok": not result.get("errors")}
            return self._finish("apatch_strip", result)
        except RuntimeTransitionError as e:
            return e.to_dict()
        except WorkflowError as e:
            return {"ok": False, "error": str(e)}

    def phase_run(
        self,
        file_path: Optional[str],
        manifest_path: str,
        *,
        skip_transition_check: bool = False,
        finish_tool: str = "apatch_phase_run",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Governed strip phase: manifest → strip/module/native → verify (RFP-004 backlog)."""
        try:
            if not skip_transition_check:
                self._ensure_mutation(
                    "phase_run",
                    intent_hint="auto: phase_run via MCP",
                )
                assert_operation(self.target_dir, OP_STRIP)
            result = workflow_phase_run(
                file_path,
                manifest_path,
                target_dir=self.target_dir,
                **kwargs,
            )
            ok = result.get("ok")
            if ok is None:
                ok = not result.get("errors")
            result = {**result, "ok": ok}
            return self._finish(finish_tool, result)
        except RuntimeTransitionError as e:
            return e.to_dict()
        except WorkflowError as e:
            return {"ok": False, "error": str(e)}

    def pipeline_run(
        self,
        manifest_path: str,
        *,
        dry_run: bool = False,
        skip_transition_check: bool = False,
    ) -> Dict[str, Any]:
        try:
            if not skip_transition_check:
                self._ensure_mutation(
                    "pipeline_run",
                    intent_hint="auto: pipeline_run via MCP",
                )
                assert_operation(self.target_dir, OP_PIPELINE)
            result = pipeline_run_manifest(
                manifest_path, self.target_dir, dry_run=dry_run
            )
            return self._finish("apatch_pipeline_run", result)
        except RuntimeTransitionError as e:
            return e.to_dict()
        except (FileNotFoundError, ValueError) as e:
            return {"ok": False, "error": str(e)}

    def replay(
        self,
        *,
        session_id: Optional[str] = None,
        session_path: Optional[str] = None,
        mode: str = "deterministic",
    ) -> Dict[str, Any]:
        try:
            assert_operation(self.target_dir, OP_REPLAY, strict=False)
            result = replay_session_workspace(
                self.target_dir,
                session_id=session_id,
                session_path=session_path,
                mode=mode,
            )
            return self._finish("apatch_replay", result)
        except RuntimeTransitionError as e:
            return e.to_dict()

    def attest(self, *, message: Optional[str] = None) -> Dict[str, Any]:
        """Commit TrustChain attestation for the current governed session."""
        from apatch.runtime.attestation import build_attestation_view
        from apatch.session_state import load_session_state
        from apatch.trustchain_helper import TrustChainHelper

        try:
            self._capture_binding("attest")
            assert_operation(self.target_dir, OP_ATTEST)
            # Reconciliation may advance the state CAS revision.
            self._capture_binding("attest")
            raw = load_session_state(self.target_dir)
            view = build_attestation_view(self.target_dir)
            intent = raw.get("intent") or message or "apatch session"
            artifacts = raw.get("artifacts") or []
            tc = TrustChainHelper(self.target_dir, auto_init=True)
            committed = False
            contribution_receipt: Optional[Dict[str, Any]] = None
            avatar_sync: Optional[Dict[str, Any]] = None
            sdd_evidence: Optional[Dict[str, Any]] = None
            if isinstance(raw.get("sdd"), dict):
                from apatch.sdd_integrity import (
                    SddContractError,
                    build_session_attestation_evidence,
                )

                try:
                    sdd_evidence = build_session_attestation_evidence(
                        self.target_dir,
                        raw,
                        list(tc.iter_ledger_entries()) if tc.has_trustchain() else [],
                    )
                except SddContractError as exc:
                    return self._finish(
                        "apatch_attest",
                        {
                            "ok": False,
                            "error_type": "SDD_PROOF_INCOMPLETE",
                            "error": str(exc),
                            "recoverable": True,
                            "recommended_action": "complete_frozen_verification",
                            "session_id": raw.get("session_id"),
                        },
                    )
            if tc.has_trustchain():
                payload: Dict[str, Any] = {
                    "intent": intent,
                    "session_id": raw.get("session_id"),
                    "message": message or intent,
                }
                if artifacts:
                    payload["artifacts"] = artifacts
                if sdd_evidence is not None:
                    payload["sdd_evidence"] = sdd_evidence
                committed = tc.commit_action("apatch_attest", payload)
                if committed:
                    # RFP-026: emit a signed per-session contribution receipt.
                    # Attestation stays authoritative, but Avatar degradation must
                    # be explicit and must never expose exception text.
                    try:
                        from apatch.contribution import emit_contribution

                        contribution = emit_contribution(
                            self.target_dir,
                            raw,
                            completion_summary=message,
                        )
                        if contribution is None:
                            contribution_receipt = {
                                "status": "not_applicable",
                                "code": "NO_MUTATIONS",
                            }
                        else:
                            contribution_receipt = {
                                "status": "emitted",
                                "event_id": contribution.get("event_id"),
                            }
                            try:
                                from apatch.avatar_delivery import sync_avatar_state

                                avatar_sync = sync_avatar_state(self.target_dir)
                            except Exception:
                                avatar_sync = {
                                    "ok": False,
                                    "status": "evidence_sync_failed",
                                    "retryable": True,
                                    "outbox_preserved": True,
                                }
                    except ImportError:
                        contribution_receipt = {
                            "status": "unavailable",
                            "code": "AVATAR_CONTRACT_UNAVAILABLE",
                        }
                    except Exception:
                        contribution_receipt = {
                            "status": "failed",
                            "code": "CONTRIBUTION_EMISSION_FAILED",
                        }
            result: Dict[str, Any] = {
                "ok": committed or not tc.has_trustchain(),
                "committed": committed,
                "attestation": view.get("attestation"),
                "intent": intent,
                "session_id": raw.get("session_id"),
                "artifacts": raw.get("artifacts") or [],
            }
            if sdd_evidence is not None:
                result["sdd_evidence"] = sdd_evidence
            if contribution_receipt is not None:
                result["contribution_receipt"] = contribution_receipt
            if avatar_sync is not None:
                result["avatar_sync"] = avatar_sync
            spec_warning = _spec_requirement_attest_warning(
                tc,
                artifacts=artifacts,
                session_id=raw.get("session_id"),
            )
            if spec_warning:
                result["spec_requirement_warning"] = spec_warning
            if tc.has_trustchain() and not committed:
                result["ok"] = False
                result["error"] = (
                    "TrustChain attestation commit failed "
                    "(policy blocked apatch_attest or Ed25519 sign error — "
                    "see stderr)"
                )
            return self._finish("apatch_attest", result)
        except (RuntimeTransitionError, SessionBindingError) as e:
            return e.to_dict()

    def noop_attest(
        self,
        covered_by,
        *,
        message: Optional[str] = None,
        evidence: Optional[Dict[str, Any]] = None,
        reverification=None,
    ) -> Dict[str, Any]:
        """RFP-027 U27-F: attest a requirement satisfied by another Rk's mutation,
        without a fabricated marker file. Records an attestation carrying
        ``covered_by`` so spec coverage counts it with no new file change.
        ``evidence`` optionally binds compact precomputed verification results
        to the same signed ledger row (used by slug ratify batch attestation)."""
        from apatch.session_state import load_session_state
        from apatch.trustchain_helper import TrustChainHelper

        try:
            self._capture_binding("noop_attest")
        except SessionBindingError as exc:
            return exc.to_dict()
        if isinstance(covered_by, str):
            covered_by = [covered_by]
        covered_by = list(covered_by or [])
        raw = load_session_state(self.target_dir)
        intent = raw.get("intent") or message or "noop attest"
        artifacts = raw.get("artifacts") or []
        tc = TrustChainHelper(self.target_dir, auto_init=True)
        payload: Dict[str, Any] = {
            "intent": intent,
            "session_id": raw.get("session_id"),
            "message": message or intent,
            "covered_by": covered_by,
        }
        if artifacts:
            payload["artifacts"] = artifacts
        if evidence:
            payload["evidence"] = evidence
        if reverification is not None:
            from apatch.spec_reverification import FileReverification, ReverificationError
            try:
                if not isinstance(reverification, FileReverification):
                    raise ReverificationError("internal pre-verification snapshot required")
                payload["file_reverification"] = reverification.payload(self.target_dir, artifacts)
                self._capture_binding("noop_attest")
            except SessionBindingError as exc:
                return exc.to_dict()
            except ReverificationError as exc:
                return self._finish("apatch_attest", {
                    "ok": False, "error_type": "REVERIFICATION_INVALID",
                    "error": str(exc), "recoverable": True,
                    "recommended_action": "reverify_current_files",
                })
        committed = (
            tc.commit_action("apatch_attest", payload) if tc.has_trustchain() else False
        )
        result = {
            "ok": committed or not tc.has_trustchain(),
            "committed": committed,
            "noop": True,
            "covered_by": covered_by,
            "intent": intent,
            "session_id": raw.get("session_id"),
            "artifacts": artifacts,
        }
        if evidence:
            result["evidence"] = evidence
        return self._finish("apatch_attest", result)

    def verification_status(self) -> Dict[str, Any]:
        from apatch.runtime.verification import build_verification_status

        return build_verification_status(self.target_dir)

    def verify_job_status(self, job_id: str) -> Dict[str, Any]:
        from apatch.verify_jobs import poll_verify_job

        result = poll_verify_job(self.target_dir, job_id)
        return self._finish("apatch_verify_status", result)

    def open_session(
        self,
        intent: str,
        *,
        artifacts: Optional[Any] = None,
        artifact_files: Optional[Dict[str, List[str]]] = None,
        request_id: Optional[str] = None,
        sdd_contract: Optional[Dict[str, Any]] = None,
        task_envelope: Optional[Dict[str, Any]] = None,
        actor: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Open governed session with explicit Intent (RFP-004)."""
        from apatch.runtime.session import start_session

        from apatch.runtime.request_journal import run_idempotent_request

        request_payload: Dict[str, Any] = {
            "intent": intent,
            "artifacts": artifacts,
            "artifact_files": artifact_files,
        }
        if any(item is not None for item in (sdd_contract, task_envelope, actor)):
            request_payload.update(
                {
                    "sdd_contract": sdd_contract,
                    "task_envelope": task_envelope,
                    "actor": actor,
                }
            )
        result = run_idempotent_request(
            self.target_dir,
            operation="session_start",
            request_id=request_id,
            payload=request_payload,
            execute=lambda: start_session(
                self.target_dir,
                intent,
                artifacts=artifacts,
                artifact_files=artifact_files,
                sdd_contract=sdd_contract,
                task_envelope=task_envelope,
                actor=actor,
            ),
        )
        if not result.get("ok"):
            return result
        self.session_id = str(result.get("session", {}).get("session_id") or "")
        self.session_token = result.get("session_token")
        self._capture_binding("session_start")
        return self._finish("apatch_session_start", result)

    def close_session(self) -> Dict[str, Any]:
        """Close only the governed session captured by this runtime."""
        from apatch.runtime.session import end_session

        result = end_session(
            self.target_dir,
            expected_session_id=self.session_id,
            session_token=self.session_token,
            require_binding=self.enforce_binding,
        )
        if not result.get("ok"):
            return result
        # Finalization atomically marks the session ended and advances its CAS
        # revision. The response write-behind must compare against that committed
        # revision, not the pre-finalization revision retained by this runtime.
        from apatch.session_state import load_session_state

        raw = load_session_state(self.target_dir)
        if str(raw.get("session_id") or "") == str(result.get("session_id") or ""):
            self._expected_revision = int(raw.get("revision") or 0)
        return self._finish("apatch_session_end", result)

    def sdd_verify(self, *, timeout: int = 300) -> Dict[str, Any]:
        """Run only the exact frozen judge bound to this governed session."""

        from apatch.sdd_integrity import (
            SddContractError,
            run_frozen_session_verification,
        )

        try:
            self._capture_binding("sdd_verify")
            assert_operation(self.target_dir, OP_VERIFY)
            self._capture_binding("sdd_verify")
            result = run_frozen_session_verification(
                self.target_dir,
                expected_session_id=self.session_id,
                timeout=timeout,
            )
            # Running the frozen judge atomically records its own proof and so
            # advances the session CAS revision. The response write-behind must
            # compare against that committed revision rather than the one this
            # runtime captured beforehand; otherwise the verifier conflicts with
            # its own write and attest can never be reached.
            from apatch.session_state import load_session_state

            raw = load_session_state(self.target_dir)
            if str(raw.get("session_id") or "") == self.session_id:
                self._expected_revision = int(raw.get("revision") or 0)
            return self._finish("apatch_sdd_verify", result)
        except SddContractError as exc:
            return self._finish(
                "apatch_sdd_verify",
                {
                    "ok": False,
                    "error_type": "SDD_VERIFIER_CONTRACT_INVALID",
                    "error": str(exc),
                    "recoverable": True,
                    "recommended_action": "repair_frozen_contract",
                },
            )
        except (RuntimeTransitionError, SessionBindingError) as exc:
            return exc.to_dict()

    def verify_run(
        self,
        *,
        verify: Optional[Any] = None,
        semantic: bool = False,
        notarization: bool = False,
        pipeline_manifest: Optional[str] = None,
        dry_run: bool = False,
        rules_path: Optional[str] = None,
        since: str = "HEAD",
        staged: bool = False,
        working_tree: bool = False,
        skip_transition_check: bool = False,
        baseline: str = "off",
        allowed_failures: Optional[List[str]] = None,
        async_mode: bool = False,
    ) -> Dict[str, Any]:
        """Facade: shell verify, semantic, notarization, or pipeline verify.

        ``verify``: shell string or argv list (argv runs without a shell — no
        quoting pitfalls). ``baseline='capture'`` snapshots failing tests BEFORE
        apply; ``baseline='compare'`` passes when no NEW failures vs the snapshot
        (pre-existing red must not block innocent mutations). ``allowed_failures``:
        node ids / substrings that never block. ``async_mode=True`` starts a
        background verify job (AR-2); poll with ``verify_job_status`` / MCP
        ``apatch_verify_status(job_id=…)``.
        """
        from apatch.tool_paths import materialize_verify_command, run_shell_verify
        from apatch.toolchain import detect_toolchain
        from apatch.workflows import verify_notarization_workspace

        if isinstance(verify, (list, tuple)):
            shell_verify: Any = [str(a) for a in verify if str(a) != ""]
        else:
            shell_verify = (verify or "").strip()
        modes = sum(bool(x) for x in (shell_verify, semantic, notarization, pipeline_manifest))
        if modes == 0:
            tc = detect_toolchain(self.target_dir)
            shell_verify = (
                tc.get("recommended_verify_resolved")
                or tc.get("recommended_verify")
                or ""
            ).strip()
            tools = tc.get("tools") or {}
            if shell_verify and (
                tools.get("npm", {}).get("found") or tools.get("pytest", {}).get("found")
            ):
                modes = 1
            else:
                semantic = True
                modes = 1
        if modes > 1:
            return {
                "ok": False,
                "error": "Choose one of verify, semantic, notarization, pipeline_manifest",
            }

        try:
            self._capture_binding("verify_run")
            if shell_verify:
                if not skip_transition_check:
                    assert_operation(self.target_dir, OP_VERIFY)
                    # Reconciliation may advance the state CAS revision.
                    self._capture_binding("verify_run")
                baseline_mode = (baseline or "off").strip().lower()
                if baseline_mode not in ("off", "capture", "compare"):
                    return {
                        "ok": False,
                        "error": "baseline must be one of: off, capture, compare",
                    }
                from apatch.verify_jobs import should_force_async, start_verify_job
                from apatch.session_state import load_session_state

                forced, force_reason = should_force_async(self.target_dir, shell_verify)
                if async_mode or forced:
                    sid = load_session_state(self.target_dir).get("session_id")
                    job = start_verify_job(
                        self.target_dir,
                        shell_verify,
                        baseline=baseline_mode,
                        allowed_failures=allowed_failures,
                        session_id=sid,
                    )
                    if forced:
                        job["async_forced"] = True
                        job["async_force_reason"] = force_reason
                    return self._finish("apatch_verify_run", job)
                ok, err = run_shell_verify(shell_verify, self.target_dir)
                resolved_cmd = materialize_verify_command(shell_verify, self.target_dir)
                result: Dict[str, Any] = {
                    "ok": ok,
                    "verify_command": shell_verify,
                    "verify_command_resolved": resolved_cmd,
                }
                if baseline_mode == "capture":
                    from apatch.verify_baseline import parse_failed_tests, save_baseline

                    failures = parse_failed_tests(err)
                    save_baseline(
                        self.target_dir,
                        verify_command=resolved_cmd,
                        failures=failures,
                        session_id=self.session_id,
                    )
                    # Capture is a pre-apply snapshot: pre-existing red must not block.
                    result["ok"] = True
                    result["baseline"] = {
                        "mode": "capture",
                        "failures": failures,
                        "verify_exit_ok": ok,
                    }
                    return self._finish("apatch_verify_run", result)
                if not ok and (baseline_mode == "compare" or allowed_failures):
                    from apatch.verify_baseline import (
                        compare_failures,
                        load_baseline,
                        parse_failed_tests,
                    )

                    current = parse_failed_tests(err)
                    base_data = (
                        load_baseline(
                            self.target_dir,
                            session_id=self.session_id,
                            verify_command=resolved_cmd,
                        )
                        if baseline_mode == "compare"
                        else None
                    )
                    report = compare_failures(
                        current,
                        baseline=(base_data or {}).get("failures") or [],
                        allowed_failures=allowed_failures or [],
                    )
                    report["mode"] = baseline_mode
                    if baseline_mode == "compare":
                        report["baseline_found"] = base_data is not None
                    if not current:
                        report["unparsed_output"] = True
                    result["baseline"] = report
                    if current and not report["new_failures"]:
                        ok = True
                        result["ok"] = True
                        result["pre_existing_only"] = True
                        result["note"] = (
                            "verify exited non-zero but every failure is "
                            "pre-existing (baseline) or allow-listed — not blocking"
                        )
                        result["verify_output"] = err
                if not ok:
                    result["error"] = err
                    result["verify_output"] = err
                    from apatch.build_diagnose import enrich_verify_failure

                    enrich_verify_failure(
                        result,
                        self.target_dir,
                        log_text=err,
                        verify=resolved_cmd,
                    )
                return self._finish("apatch_verify_run", result)

            if pipeline_manifest:
                if not skip_transition_check:
                    assert_operation(self.target_dir, OP_PIPELINE)
                    self._capture_binding("verify_run")
                result = pipeline_run_manifest(
                    pipeline_manifest, self.target_dir, dry_run=dry_run
                )
                return self._finish("apatch_verify_run", result)

            if notarization:
                if not staged and not working_tree:
                    staged = True
                result = verify_notarization_workspace(
                    self.target_dir,
                    staged=staged,
                    working_tree=working_tree,
                )
                return self._finish("apatch_verify_run", result)

            from apatch.workflows import semantic_verify_workspace

            if not skip_transition_check:
                assert_operation(self.target_dir, OP_VERIFY)
                self._capture_binding("verify_run")
            result = semantic_verify_workspace(
                self.target_dir, rules_path=rules_path, since=since
            )
            return self._finish("apatch_verify_run", result)
        except (RuntimeTransitionError, SessionBindingError) as e:
            return e.to_dict()
        except (FileNotFoundError, RuntimeError, ValueError, WorkflowError) as e:
            return self._finish("apatch_verify_run", {"ok": False, "error": str(e)})

    def export_attestation(
        self,
        out_path: str,
        *,
        event_limit: int = 500,
    ) -> Dict[str, Any]:
        """Export session + attestation + events audit bundle."""
        from apatch.runtime.attestation import export_attestation_bundle

        result = export_attestation_bundle(
            self.target_dir, out_path, event_limit=event_limit
        )
        return self._finish("apatch_attestation_export", result)

    def events_tail(self, limit: int = 20) -> Dict[str, Any]:
        """Read-only tail of .apatch/events.jsonl."""
        from apatch.runtime.events import build_events_tail_view

        result = build_events_tail_view(self.target_dir, limit=limit)
        return self._finish("apatch_events_tail", result)
