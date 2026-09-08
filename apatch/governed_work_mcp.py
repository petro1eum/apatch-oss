"""MCP-facing orchestration for TrustChain governed work (RFP-043).

The domain documents stay in :mod:`apatch.governed_work`; this module only
turns them into bounded, actionable operations for the full MCP profile.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Sequence

from apatch import governed_work as G
from apatch import governed_work_delivery as D
from apatch import governed_work_transport as T


def _failure(exc: Exception, *, operation: str) -> Dict[str, Any]:
    return {
        "ok": False,
        "operation": operation,
        "error_type": "GOVERNED_WORK_INVALID",
        "error": str(exc),
        "recoverable": True,
    }


def configure_governed_work(
    target_dir: str,
    *,
    platform_url: str,
    client_id: str,
    binding_authority_keys: Mapping[str, str],
    request_key_provider: Any = None,
) -> Dict[str, Any]:
    """Persist public connection metadata derived from the enrolled identity."""
    try:
        public_identity = T.service_request_identity(
            target_dir, key_provider=request_key_provider
        )
        stored = D.write_config(
            target_dir,
            {
                "schema": D.CONFIG_SCHEMA,
                "platform_url": platform_url,
                "client_id": client_id,
                "service_request_key_id": public_identity[
                    "service_request_key_id"
                ],
                "binding_authority_keys": dict(binding_authority_keys),
            },
        )
        return {
            **stored,
            "public_registration": public_identity,
        }
    except (G.GovernedWorkError, OSError, ValueError) as exc:
        return _failure(exc, operation="configure")


def transition_governed_work_endpoint(
    target_dir: str,
    *,
    expected_platform_url: str,
    platform_url: str,
    idempotency_key: str,
    request_key_provider: Any = None,
) -> Dict[str, Any]:
    """Safely move immutable governed-work config to another signed endpoint."""
    try:
        return {
            "operation": "transition_endpoint",
            **D.transition_platform_url(
                target_dir,
                expected_platform_url=expected_platform_url,
                platform_url=platform_url,
                idempotency_key=idempotency_key,
                key_provider=request_key_provider,
            ),
        }
    except (G.GovernedWorkError, OSError, ValueError) as exc:
        return _failure(exc, operation="transition_endpoint")


def _optional_config(target_dir: str) -> tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    try:
        config = D.load_config(target_dir)
    except G.GovernedWorkError:
        return None, {
            "status": "config_required",
            "next_action": "apatch_governed_work_configure",
        }
    return config, {"status": "ready"}


def prepare_governed_change(
    target_dir: str,
    *,
    tenant_id: str,
    project_group_id: str,
    work_program_id: str,
    work_program_hash: str,
    spec_id: str,
    purpose: str,
    requirement_ids: Optional[Sequence[str]] = None,
    spec_path: Optional[str] = None,
    context_release_id: Optional[str] = None,
    context_release_manifest_hash: Optional[str] = None,
    queue_source_binding: bool = True,
) -> Dict[str, Any]:
    """Create the signed Change and optionally persist its Platform request."""
    try:
        prepared = G.prepare_change(
            target_dir,
            tenant_id=tenant_id,
            project_group_id=project_group_id,
            work_program_id=work_program_id,
            work_program_hash=work_program_hash,
            spec_id=spec_id,
            purpose=purpose,
            requirement_ids=requirement_ids,
            spec_path=spec_path,
            context_release_id=context_release_id,
            context_release_manifest_hash=context_release_manifest_hash,
        )
        delivery: Dict[str, Any] = {"status": "not_requested"}
        if queue_source_binding:
            config, delivery = _optional_config(target_dir)
            if config is not None:
                delivery = {
                    "status": "queued",
                    **D.queue_source_binding_request(
                        target_dir,
                        change=prepared["change"],
                        client_id=config["client_id"],
                    ),
                }
        change = prepared["change"]
        return {
            "ok": True,
            "operation": "prepare_change",
            "stored": prepared["stored"],
            "change": change,
            "change_id": change["change_id"],
            "change_hash": G.document_hash(change),
            "source_binding_delivery": delivery,
        }
    except (G.GovernedWorkError, OSError, ValueError) as exc:
        return _failure(exc, operation="prepare_change")


def store_governed_source_binding(
    target_dir: str,
    *,
    binding: Mapping[str, Any],
    status: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Verify a Platform binding against its exact local Change and store it."""
    try:
        config = D.load_config(target_dir)
        return G.store_project_source_binding(
            target_dir,
            binding,
            trusted_authority_keys=config["binding_authority_keys"],
            status=status,
        )
    except (G.GovernedWorkError, OSError, ValueError) as exc:
        return _failure(exc, operation="store_source_binding")


def build_governed_evidence(
    target_dir: str,
    *,
    binding_id: str,
    contribution_store_dir: Optional[str] = None,
    queue_for_admission: bool = True,
) -> Dict[str, Any]:
    """Build signed evidence/timesheet facts and optionally queue admission."""
    try:
        built = G.build_workspace_evidence(
            target_dir,
            binding_id=binding_id,
            contribution_store_dir=contribution_store_dir,
        )
        delivery: Dict[str, Any] = {"status": "not_requested"}
        if queue_for_admission:
            config, delivery = _optional_config(target_dir)
            if config is not None:
                change = G.load_change(target_dir, built["change_id"])
                delivery = {
                    "status": "queued",
                    **D.queue_evidence_admission(
                        target_dir,
                        evidence_bundle=built["evidence_bundle"],
                        timesheet_draft=built["timesheet"],
                        tenant_id=change["tenant_id"],
                        project_group_id=change["project_group_id"],
                        client_id=config["client_id"],
                    ),
                }
        return {
            **built,
            "operation": "build_evidence",
            "evidence_admission_delivery": delivery,
        }
    except (G.GovernedWorkError, OSError, ValueError) as exc:
        return _failure(exc, operation="build_evidence")


def sync_governed_work(
    target_dir: str,
    *,
    platform_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Deliver immutable outbox entries; credentials come only from configured env."""
    try:
        return {
            "operation": "sync",
            **D.sync_governed_work(target_dir, platform_url=platform_url),
        }
    except (G.GovernedWorkError, OSError, ValueError) as exc:
        return _failure(exc, operation="sync")


def retire_governed_work_outbox(
    target_dir: str,
    *,
    entry_id: str,
    superseded_by_entry_id: str,
) -> Dict[str, Any]:
    """Retire a rejected request only after its replacement has a validated ACK."""
    try:
        return {
            "operation": "retire_outbox",
            **D.retire_outbox_entry(
                target_dir,
                entry_id=entry_id,
                superseded_by_entry_id=superseded_by_entry_id,
            ),
        }
    except (G.GovernedWorkError, OSError, ValueError) as exc:
        return _failure(exc, operation="retire_outbox")


def _fetch_platform_status(
    target_dir: str,
    *,
    tenant_id: str,
    project_group_id: str,
    work_program_id: str,
    config: Mapping[str, Any],
    http_client: Any = None,
    timeout: float = 20.0,
    request_key_provider: Any = None,
) -> Dict[str, Any]:
    if not tenant_id or not project_group_id or not work_program_id:
        return {
            "status": "input_required",
            "required": ["tenant_id", "project_group_id", "work_program_id"],
        }
    path = (
        f"/api/internal/project-groups/{project_group_id}/"
        f"governed-work/status/{work_program_id}"
    )
    try:
        signed_headers = T.signed_service_headers(
            target_dir,
            method="GET",
            path=path,
            raw_body=b"",
            tenant_id=tenant_id,
            subject=str(config["client_id"]),
            expected_key_id=str(config["service_request_key_id"]),
            key_provider=request_key_provider,
        )
    except G.GovernedWorkError as exc:
        return {
            "status": "credential_unavailable",
            "service_request_key_id": config["service_request_key_id"],
            "detail": str(exc),
        }
    if http_client is None and D.httpx is None:
        return {"status": "transport_unavailable"}
    client = http_client or D.httpx.Client(timeout=timeout)
    close = http_client is None
    try:
        response = client.get(
            str(config["platform_url"]).rstrip("/") + path,
            params={
                "subject": config["client_id"],
                "tenant_id": tenant_id,
            },
            headers={
                "X-Apatch-Client-Id": config["client_id"],
                **signed_headers,
            },
        )
        if int(response.status_code) != 200:
            return {
                "status": "platform_rejected",
                "status_code": int(response.status_code),
            }
        body = response.json()
        if not isinstance(body, dict):
            raise G.GovernedWorkError("Platform status must be an object")
        projection = D.validate_status_projection(body)
        expected = {
            "tenant_id": tenant_id,
            "project_group_id": project_group_id,
            "work_program_id": work_program_id,
        }
        for field, value in expected.items():
            if projection[field] != value:
                raise G.GovernedWorkError(f"Platform status scope mismatch: {field}")
        return {"status": "available", "projection": projection}
    except G.GovernedWorkError:
        raise
    except Exception:
        return {"status": "platform_unavailable"}
    finally:
        if close:
            client.close()


def governed_work_status(
    target_dir: str,
    *,
    tenant_id: str = "",
    project_group_id: str = "",
    work_program_id: str = "",
    refresh_platform: bool = False,
    http_client: Any = None,
    request_key_provider: Any = None,
) -> Dict[str, Any]:
    """Return independent local, delivery and optional Platform projections."""
    try:
        local = G.local_governed_work_status(target_dir)
        delivery = D.governed_work_delivery_status(target_dir)
        platform: Dict[str, Any] = {"status": "not_requested"}
        if refresh_platform:
            config = D.load_config(target_dir)
            platform = _fetch_platform_status(
                target_dir,
                tenant_id=tenant_id,
                project_group_id=project_group_id,
                work_program_id=work_program_id,
                config=config,
                http_client=http_client,
                request_key_provider=request_key_provider,
            )
        return {
            "ok": True,
            "operation": "status",
            "schema": "apatch.governed-work-mcp-status.v1",
            "local": local,
            "delivery": delivery,
            "platform": platform,
        }
    except (G.GovernedWorkError, OSError, ValueError) as exc:
        return _failure(exc, operation="status")
