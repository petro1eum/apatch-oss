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




SHARE_PLAN_SCHEMA = "apatch.cowork-share-plan.v1"
_SHARE_SCOPE = "governed_work.evidence_admission"


SHARING_FENCE_SCHEMA = "apatch.cowork-sharing-fence.v1"
_SHARING_FENCE_KEYS = frozenset(
    {
        "schema",
        "state",
        "connection_generation",
        "pending_entries",
        "disconnected_at",
    }
)
_SHARING_FENCE_ENTRY_KEYS = frozenset({"entry_id", "request_hash"})


def _connection_generation(config: Mapping[str, Any]) -> str:
    return G.value_hash(dict(config))


def _sharing_fence_dir(target_dir: str):
    return G.governed_work_root(target_dir) / "sharing_fences"


def _sharing_fences(target_dir: str) -> list[Dict[str, Any]]:
    fences = []
    root = _sharing_fence_dir(target_dir)
    if not root.is_dir():
        return fences
    for path in sorted(root.glob("*.json")):
        fence = G._expect_exact_keys(
            G._read_document(path),
            _SHARING_FENCE_KEYS,
            "sharing fence",
        )
        if (
            fence["schema"] != SHARING_FENCE_SCHEMA
            or fence["state"] != "disconnected"
        ):
            raise G.GovernedWorkError("invalid sharing fence")
        G._required_text(
            fence["connection_generation"],
            "sharing fence connection_generation",
        )
        G._timestamp(fence["disconnected_at"], "sharing fence disconnected_at")
        if not isinstance(fence["pending_entries"], list):
            raise G.GovernedWorkError("sharing fence pending_entries must be a list")
        for item in fence["pending_entries"]:
            ref = G._expect_exact_keys(
                item,
                _SHARING_FENCE_ENTRY_KEYS,
                "sharing fence pending entry",
            )
            G._required_text(ref["entry_id"], "sharing fence entry_id")
            G._required_text(ref["request_hash"], "sharing fence request_hash")
        fences.append(fence)
    return fences


def _pending_outbox_entries(target_dir: str) -> list[Dict[str, Any]]:
    return [
        entry
        for path, entry in D._iter_outbox(target_dir)
        if not D._ack_path(path).exists()
        and not D._retirement_is_active(target_dir, path, entry)
    ]


def _assert_sharing_available(
    target_dir: str,
    config: Mapping[str, Any],
    *,
    bundle_id: Optional[str] = None,
) -> None:
    generation = _connection_generation(config)
    pending = {
        entry["entry_id"]: entry
        for entry in _pending_outbox_entries(target_dir)
    }
    for fence in _sharing_fences(target_dir):
        if fence["connection_generation"] == generation:
            raise G.GovernedWorkError(
                "governed-work sharing is disconnected for this connection generation"
            )
        fenced_ids = {item["entry_id"] for item in fence["pending_entries"]}
        if fenced_ids & set(pending):
            raise G.GovernedWorkError(
                "a disconnected connection generation has pending evidence"
            )
        if bundle_id is not None:
            for entry in pending.values():
                payload = entry.get("payload", {})
                bundle = payload.get("evidence_bundle", {})
                if (
                    entry["entry_id"] in fenced_ids
                    and bundle.get("bundle_id") == bundle_id
                ):
                    raise G.GovernedWorkError(
                        "evidence belongs to a disconnected connection generation"
                    )


def disconnect_governed_work(
    target_dir: str,
    *,
    disconnected_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Fence current and pending sharing without deleting local history."""
    try:
        config = D.load_config(target_dir)
        generation = _connection_generation(config)
        timestamp = G._canonical_timestamp(
            disconnected_at or G._utc_now(),
            "disconnected_at",
        )
        entries = [
            {
                "entry_id": str(entry["entry_id"]),
                "request_hash": str(entry["request_hash"]),
            }
            for entry in _pending_outbox_entries(target_dir)
        ]
        fence = {
            "schema": SHARING_FENCE_SCHEMA,
            "state": "disconnected",
            "connection_generation": generation,
            "pending_entries": entries,
            "disconnected_at": timestamp,
        }
        path = _sharing_fence_dir(target_dir) / (
            generation.split(":", 1)[-1] + ".json"
        )
        if path.is_file():
            existing = G._read_document(path)
            if (
                existing.get("schema") != SHARING_FENCE_SCHEMA
                or existing.get("state") != "disconnected"
                or existing.get("connection_generation") != generation
            ):
                raise G.GovernedWorkError("invalid current sharing fence")
            stored = False
            fence = existing
        else:
            stored = G._immutable_write(path, fence)
        return {
            "ok": True,
            "operation": "disconnect",
            "state": "disconnected",
            "stored": stored,
            "connection_generation": generation,
            "pending_fenced": len(fence["pending_entries"]),
        }
    except (G.GovernedWorkError, OSError, TypeError, ValueError) as exc:
        return _failure(exc, operation="disconnect")


def _publication_documents(
    target_dir: str,
    binding_id: str,
) -> tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    root = G.governed_work_root(target_dir)
    provider = G._load_local_provider(target_dir)
    key_id = G.signer_key_id(provider)
    trusted = {key_id: provider.get_public_key()}
    matches = []
    evidence_dir = root / "evidence"
    if evidence_dir.is_dir():
        for path in sorted(evidence_dir.glob("*.json")):
            bundle = G.validate_work_evidence_bundle(
                G._read_document(path),
                trusted_actor_keys=trusted,
            )
            if bundle["project_source_binding_id"] == binding_id:
                matches.append(bundle)
    if len(matches) != 1:
        raise G.GovernedWorkError(
            "exactly one current evidence bundle is required for the binding"
        )
    bundle = matches[0]
    timesheet_id = bundle["timesheet_ref"]["timesheet_id"]
    timesheet = G.validate_timesheet_draft(
        G._read_document(root / "timesheets" / f"{timesheet_id}.json"),
        trusted_actor_keys=trusted,
    )
    if (
        G.document_hash(timesheet) != bundle["timesheet_ref"]["timesheet_hash"]
        or timesheet["claimed_active_seconds"]
        != bundle["timesheet_ref"]["claimed_active_seconds"]
        or timesheet["project_source_binding_id"] != binding_id
    ):
        raise G.GovernedWorkError("evidence bundle and timesheet selection differ")
    change = G.load_change(target_dir, bundle["change_id"])
    if (
        G.document_hash(change) != bundle["change_hash"]
        or change["tenant_id"] == ""
        or change["project_group_id"] == ""
    ):
        raise G.GovernedWorkError("evidence bundle and Change scope differ")
    return change, bundle, timesheet


def _share_plan(
    *,
    config: Mapping[str, Any],
    change: Mapping[str, Any],
    bundle: Mapping[str, Any],
    timesheet: Mapping[str, Any],
) -> Dict[str, Any]:
    body = {
        "schema": SHARE_PLAN_SCHEMA,
        "platform_origin": str(config["platform_url"]),
        "client_subject": str(config["client_id"]),
        "tenant_id": str(change["tenant_id"]),
        "project_group_id": str(change["project_group_id"]),
        "scope": _SHARE_SCOPE,
        "evidence_bundle_ref": {
            "bundle_id": str(bundle["bundle_id"]),
            "bundle_hash": G.document_hash(bundle),
        },
        "timesheet_ref": {
            "timesheet_id": str(timesheet["timesheet_id"]),
            "timesheet_hash": G.document_hash(timesheet),
            "claimed_active_seconds": int(timesheet["claimed_active_seconds"]),
        },
        "connection_generation": G.value_hash(dict(config)),
    }
    return {**body, "plan_hash": G.value_hash(body)}


def preview_governed_evidence_publication(
    target_dir: str,
    *,
    binding_id: str,
) -> Dict[str, Any]:
    """Build a content-free, read-only publication plan for exact local evidence."""
    try:
        config = D.load_config(target_dir)
        change, bundle, timesheet = _publication_documents(target_dir, binding_id)
        return {
            "ok": True,
            "operation": "preview_evidence_publication",
            "plan": _share_plan(
                config=config,
                change=change,
                bundle=bundle,
                timesheet=timesheet,
            ),
        }
    except (G.GovernedWorkError, OSError, ValueError) as exc:
        return _failure(exc, operation="preview_evidence_publication")


_SHARE_PLAN_KEYS = frozenset(
    {
        "schema",
        "platform_origin",
        "client_subject",
        "tenant_id",
        "project_group_id",
        "scope",
        "evidence_bundle_ref",
        "timesheet_ref",
        "connection_generation",
        "plan_hash",
    }
)
_EVIDENCE_REF_KEYS = frozenset({"bundle_id", "bundle_hash"})
_TIMESHEET_REF_KEYS = frozenset(
    {"timesheet_id", "timesheet_hash", "claimed_active_seconds"}
)


def _validated_share_plan(raw: Mapping[str, Any]) -> Dict[str, Any]:
    plan = G._expect_exact_keys(dict(raw), _SHARE_PLAN_KEYS, "share plan")
    evidence_ref = G._expect_exact_keys(
        plan["evidence_bundle_ref"],
        _EVIDENCE_REF_KEYS,
        "share plan evidence reference",
    )
    timesheet_ref = G._expect_exact_keys(
        plan["timesheet_ref"],
        _TIMESHEET_REF_KEYS,
        "share plan timesheet reference",
    )
    for key in (
        "platform_origin",
        "client_subject",
        "tenant_id",
        "project_group_id",
        "scope",
        "connection_generation",
        "plan_hash",
    ):
        G._required_text(plan[key], f"share plan {key}")
    G._required_text(evidence_ref["bundle_id"], "share plan bundle_id")
    G._required_text(evidence_ref["bundle_hash"], "share plan bundle_hash")
    G._required_text(timesheet_ref["timesheet_id"], "share plan timesheet_id")
    G._required_text(timesheet_ref["timesheet_hash"], "share plan timesheet_hash")
    claimed = timesheet_ref["claimed_active_seconds"]
    if isinstance(claimed, bool) or not isinstance(claimed, int) or claimed < 0:
        raise G.GovernedWorkError(
            "share plan claimed_active_seconds must be a non-negative integer"
        )
    return plan


def _publication_documents_for_plan(
    target_dir: str,
    plan: Mapping[str, Any],
) -> tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    bundle_id = str(plan["evidence_bundle_ref"]["bundle_id"])
    root = G.governed_work_root(target_dir)
    provider = G._load_local_provider(target_dir)
    key_id = G.signer_key_id(provider)
    trusted = {key_id: provider.get_public_key()}
    matches = []
    evidence_dir = root / "evidence"
    if evidence_dir.is_dir():
        for path in sorted(evidence_dir.glob("*.json")):
            bundle = G.validate_work_evidence_bundle(
                G._read_document(path),
                trusted_actor_keys=trusted,
            )
            if bundle["bundle_id"] == bundle_id:
                matches.append(bundle)
    if len(matches) != 1:
        raise G.GovernedWorkError(
            "share plan must reference exactly one current evidence bundle"
        )
    return _publication_documents(
        target_dir,
        str(matches[0]["project_source_binding_id"]),
    )


def publish_governed_evidence(
    target_dir: str,
    *,
    plan: Mapping[str, Any],
    confirmation: str,
) -> Dict[str, Any]:
    """Queue exactly the current previewed evidence after explicit confirmation."""
    try:
        candidate = _validated_share_plan(plan)
        expected_confirmation = f"publish:{candidate['plan_hash']}"
        if confirmation != expected_confirmation:
            raise G.GovernedWorkError(
                "exact publish:<plan_hash> confirmation required"
            )
        config = D.load_config(target_dir)
        _assert_sharing_available(target_dir, config)
        change, bundle, timesheet = _publication_documents_for_plan(
            target_dir,
            candidate,
        )
        _assert_sharing_available(
            target_dir,
            config,
            bundle_id=str(bundle["bundle_id"]),
        )
        current = _share_plan(
            config=config,
            change=change,
            bundle=bundle,
            timesheet=timesheet,
        )
        if G.canonical_bytes(candidate) != G.canonical_bytes(current):
            raise G.GovernedWorkError(
                "share plan is stale or differs from current local state"
            )
        queued = D.queue_evidence_admission(
            target_dir,
            evidence_bundle=bundle,
            timesheet_draft=timesheet,
            tenant_id=str(change["tenant_id"]),
            project_group_id=str(change["project_group_id"]),
            client_id=str(config["client_id"]),
        )
        return {
            "ok": True,
            "operation": "publish_evidence",
            "plan_hash": current["plan_hash"],
            "delivery": {"status": "queued", **queued},
        }
    except (G.GovernedWorkError, OSError, TypeError, ValueError) as exc:
        return _failure(exc, operation="publish_evidence")


def read_execution_proposal(target_dir: str, *, tenant_id: str, project_group_id: str, work_item_id: str, work_item_hash: str, authority_version: int, work_program_id: str, work_program_hash: str, idempotency_key: str, http_client: Any = None, request_key_provider: Any = None, now: Any = None) -> Dict[str, Any]:
    """Read a signed proposal; never persist it or start a session."""
    try:
        return D.fetch_execution_proposal(target_dir,tenant_id=tenant_id,project_group_id=project_group_id,work_item_id=work_item_id,work_item_hash=work_item_hash,authority_version=authority_version,work_program_id=work_program_id,work_program_hash=work_program_hash,idempotency_key=idempotency_key,http_client=http_client,request_key_provider=request_key_provider,now=now)
    except (G.GovernedWorkError,OSError,ValueError) as exc:
        return _failure(exc,operation="read_execution_proposal")


def accept_execution_proposal(target_dir: str, *, proposal: Mapping[str, Any], confirmation: str, spec_id: str, purpose: str, requirement_ids: Optional[Sequence[str]] = None, spec_path: Optional[str] = None, queue_source_binding: bool = True, request_key_provider: Any = None, now: Any = None) -> Dict[str, Any]:
    """Explicitly bind a transient proposal to a local Change; no session opens."""
    try:
        from datetime import datetime,timezone
        from apatch import execution_proposal as P
        config = D.load_config(target_dir)
        exact = P.validate_execution_proposal(proposal,trusted_keys=config["binding_authority_keys"],now=now)
        expected = f"{exact['work_item_id']}:{exact['authority_version']}"
        if confirmation != expected:
            raise G.GovernedWorkError("exact work item and authority version confirmation required")
        prepared = G.prepare_change(target_dir,tenant_id=exact["tenant_id"],project_group_id=exact["project_group_id"],work_program_id=exact["work_program_id"],work_program_hash=exact["work_program_hash"],spec_id=spec_id,purpose=purpose,requirement_ids=requirement_ids,spec_path=spec_path,key_provider=request_key_provider)
        provider = G._load_local_provider(target_dir,request_key_provider)
        accepted_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat().replace("+00:00","Z")
        accepted = P.store_acceptance(target_dir,proposal=exact,change=prepared["change"],accepted_at=accepted_at,key_provider=provider)
        delivery: Dict[str,Any] = {"status":"not_requested"}
        if queue_source_binding:
            delivery = {"status":"queued",**D.queue_work_item_acceptance(target_dir,proposal_acceptance=accepted["acceptance"],change=prepared["change"],client_id=config["client_id"])}
        return {"ok":True,"operation":"accept_execution_proposal","work_started":False,"governed_session_id":None,"change":prepared["change"],"change_hash":prepared["change_hash"],"proposal_acceptance":accepted["acceptance"],"stored":accepted["stored"],"work_item_acceptance_delivery":delivery,"source_binding_delivery":{"status":"superseded_by_work_item_acceptance"},"next_action":"sync the canonical Work Item acceptance, then open a governed session for the selected local SPEC"}
    except (G.GovernedWorkError,OSError,ValueError) as exc:
        return _failure(exc,operation="accept_execution_proposal")


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
    queue_for_admission: bool = False,
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
    http_client: Any = None,
    request_key_provider: Any = None,
) -> Dict[str, Any]:
    """Deliver immutable outbox entries; credentials come only from configured env."""
    try:
        config = D.load_config(target_dir)
        _assert_sharing_available(target_dir, config)
        return {
            "operation": "sync",
            **D.sync_governed_work(
                target_dir,
                platform_url=platform_url,
                config=config,
                http_client=http_client,
                request_key_provider=request_key_provider,
            ),
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
