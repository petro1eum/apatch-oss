from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RFP = ROOT / "docs" / "RFP-047-COWORK-SELECTIVE-DELIVERY.md"
SPEC = ROOT / "docs" / "specs" / "SPEC-COWORK-SELECTIVE-DELIVERY-1.md"
PUBLIC_OPERATIONS = {
    "preview_governed_evidence_publication",
    "publish_governed_evidence",
    "disconnect_governed_work",
}


def test_r0_traceability_and_surface() -> None:
    rfp = RFP.read_text(encoding="utf-8")
    spec = SPEC.read_text(encoding="utf-8")
    acceptance = re.findall(r"^\| (CSD-\d+) \| MUST \|", rfp, re.MULTILINE)
    mappings = re.findall(
        r"^\| (CSD-\d+) \| (R\d+) \| covered \|$",
        spec,
        re.MULTILINE,
    )
    assert acceptance == [f"CSD-{index}" for index in range(1, 9)]
    assert mappings == [
        (f"CSD-{index}", f"R{index}") for index in range(1, 9)
    ]
    assert len(set(acceptance)) == len(acceptance)
    assert len({requirement for _row, requirement in mappings}) == len(mappings)
    assert "TODO" not in spec
    assert "verify: true" not in spec
    assert PUBLIC_OPERATIONS <= {
        token.strip(chr(96)) for token in re.findall(r"\`[a-z_]+\`", spec)
    }


def test_r1_build_is_local_first(monkeypatch, tmp_path: Path) -> None:
    import inspect

    from apatch import governed_work_mcp as orchestrator

    parameter = inspect.signature(
        orchestrator.build_governed_evidence
    ).parameters["queue_for_admission"]
    assert parameter.default is False

    built = {
        "change_id": "apchg_" + "1" * 32,
        "evidence_bundle": {"schema": "apatch.work-evidence-bundle.v1"},
        "timesheet": {"schema": "apatch.timesheet-draft.v1"},
    }
    monkeypatch.setattr(
        orchestrator.G,
        "build_workspace_evidence",
        lambda *_args, **_kwargs: built,
    )

    def forbidden_queue(*_args, **_kwargs):
        raise AssertionError("local evidence build must not queue publication")

    monkeypatch.setattr(
        orchestrator.D,
        "queue_evidence_admission",
        forbidden_queue,
    )
    before = {
        str(path.relative_to(tmp_path)): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    result = orchestrator.build_governed_evidence(
        str(tmp_path),
        binding_id="tcpsb_" + "2" * 32,
    )
    after = {
        str(path.relative_to(tmp_path)): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert result == {
        **built,
        "operation": "build_evidence",
        "evidence_admission_delivery": {"status": "not_requested"},
    }
    assert after == before
    assert not (tmp_path / ".apatch" / "session_state.json").exists()


def test_r2_preview_is_exact_content_free_and_read_only(
    monkeypatch, tmp_path: Path
) -> None:
    from apatch import governed_work_mcp as orchestrator

    config = {
        "schema": "apatch.governed-work-config.v1",
        "platform_url": "https://clients.trust-chain.ai",
        "client_id": "member:developer",
        "service_request_key_id": "sha256:" + "1" * 64,
        "binding_authority_keys": {"platform:key": "public"},
    }
    change = {
        "tenant_id": "tenant-a",
        "project_group_id": "tcpg_" + "2" * 32,
    }
    bundle = {
        "bundle_id": "apweb_" + "3" * 32,
        "signature": {"value": "must-not-leak"},
    }
    timesheet = {
        "timesheet_id": "apts_" + "4" * 32,
        "claimed_active_seconds": 321,
        "signature": {"value": "must-not-leak"},
    }
    monkeypatch.setattr(orchestrator.D, "load_config", lambda _root: config)
    monkeypatch.setattr(
        orchestrator,
        "_publication_documents",
        lambda _root, _binding: (change, bundle, timesheet),
    )
    before = list(tmp_path.rglob("*"))
    result = orchestrator.preview_governed_evidence_publication(
        str(tmp_path),
        binding_id="tcpsb_" + "5" * 32,
    )
    body = {
        "schema": "apatch.cowork-share-plan.v1",
        "platform_origin": "https://clients.trust-chain.ai",
        "client_subject": "member:developer",
        "tenant_id": "tenant-a",
        "project_group_id": "tcpg_" + "2" * 32,
        "scope": "governed_work.evidence_admission",
        "evidence_bundle_ref": {
            "bundle_id": "apweb_" + "3" * 32,
            "bundle_hash": orchestrator.G.document_hash(bundle),
        },
        "timesheet_ref": {
            "timesheet_id": "apts_" + "4" * 32,
            "timesheet_hash": orchestrator.G.document_hash(timesheet),
            "claimed_active_seconds": 321,
        },
        "connection_generation": orchestrator.G.value_hash(config),
    }
    assert result == {
        "ok": True,
        "operation": "preview_evidence_publication",
        "plan": {**body, "plan_hash": orchestrator.G.value_hash(body)},
    }
    assert list(tmp_path.rglob("*")) == before
    encoded = repr(result)
    assert "must-not-leak" not in encoded
    assert "signature" not in encoded
    assert "payload" not in encoded


def test_r3_publish_requires_exact_current_confirmation(
    monkeypatch, tmp_path: Path
) -> None:
    from copy import deepcopy

    from apatch import governed_work_mcp as orchestrator

    config = {
        "schema": "apatch.governed-work-config.v1",
        "platform_url": "https://clients.trust-chain.ai",
        "client_id": "member:developer",
        "service_request_key_id": "sha256:" + "1" * 64,
        "binding_authority_keys": {"platform:key": "public"},
    }
    change = {
        "tenant_id": "tenant-a",
        "project_group_id": "tcpg_" + "2" * 32,
    }
    bundle = {"bundle_id": "apweb_" + "3" * 32}
    timesheet = {
        "timesheet_id": "apts_" + "4" * 32,
        "claimed_active_seconds": 321,
    }
    plan = orchestrator._share_plan(
        config=config,
        change=change,
        bundle=bundle,
        timesheet=timesheet,
    )
    monkeypatch.setattr(orchestrator.D, "load_config", lambda _root: config)
    monkeypatch.setattr(
        orchestrator,
        "_publication_documents_for_plan",
        lambda _root, _plan: (change, bundle, timesheet),
    )
    queued = []

    def queue(*_args, **kwargs):
        queued.append(kwargs)
        return {
            "ok": True,
            "stored": True,
            "entry_id": "apgwo_" + "6" * 32,
            "request_hash": "sha256:" + "7" * 64,
            "pending": True,
        }

    monkeypatch.setattr(orchestrator.D, "queue_evidence_admission", queue)

    variants = []
    unknown = deepcopy(plan)
    unknown["payload"] = {"secret": "must-not-pass"}
    variants.append(unknown)
    changed_recipient = deepcopy(plan)
    changed_recipient["project_group_id"] = "tcpg_" + "8" * 32
    variants.append(changed_recipient)
    changed_time = deepcopy(plan)
    changed_time["timesheet_ref"]["claimed_active_seconds"] = 322
    variants.append(changed_time)
    changed_generation = deepcopy(plan)
    changed_generation["connection_generation"] = "sha256:" + "9" * 64
    variants.append(changed_generation)

    for candidate in variants:
        result = orchestrator.publish_governed_evidence(
            str(tmp_path),
            plan=candidate,
            confirmation=f"publish:{candidate['plan_hash']}",
        )
        assert result["ok"] is False
        assert result["error_type"] == "GOVERNED_WORK_INVALID"
    wrong_confirmation = orchestrator.publish_governed_evidence(
        str(tmp_path),
        plan=plan,
        confirmation="publish:sha256:" + "0" * 64,
    )
    assert wrong_confirmation["ok"] is False
    assert queued == []

    accepted = orchestrator.publish_governed_evidence(
        str(tmp_path),
        plan=plan,
        confirmation=f"publish:{plan['plan_hash']}",
    )
    assert accepted == {
        "ok": True,
        "operation": "publish_evidence",
        "plan_hash": plan["plan_hash"],
        "delivery": {
            "status": "queued",
            "ok": True,
            "stored": True,
            "entry_id": "apgwo_" + "6" * 32,
            "request_hash": "sha256:" + "7" * 64,
            "pending": True,
        },
    }
    assert len(queued) == 1


def test_r4_publish_queues_once_without_scope_expansion(
    monkeypatch, tmp_path: Path
) -> None:
    from apatch import governed_work_mcp as orchestrator

    config = {
        "schema": "apatch.governed-work-config.v1",
        "platform_url": "https://clients.trust-chain.ai",
        "client_id": "member:developer",
        "service_request_key_id": "sha256:" + "1" * 64,
        "binding_authority_keys": {"platform:key": "public"},
    }
    change = {
        "tenant_id": "tenant-a",
        "project_group_id": "tcpg_" + "2" * 32,
    }
    timesheet = {
        "timesheet_id": "apts_" + "4" * 32,
        "claimed_active_seconds": 321,
    }
    bundle = {
        "bundle_id": "apweb_" + "3" * 32,
        "timesheet_ref": {
            "timesheet_id": timesheet["timesheet_id"],
            "timesheet_hash": orchestrator.G.document_hash(timesheet),
        },
    }
    plan = orchestrator._share_plan(
        config=config,
        change=change,
        bundle=bundle,
        timesheet=timesheet,
    )
    monkeypatch.setattr(orchestrator.D, "load_config", lambda _root: config)
    monkeypatch.setattr(
        orchestrator,
        "_publication_documents_for_plan",
        lambda _root, _plan: (change, bundle, timesheet),
    )
    monkeypatch.setattr(
        orchestrator.D,
        "validate_work_evidence_bundle",
        lambda document: dict(document),
    )
    monkeypatch.setattr(
        orchestrator.D,
        "validate_timesheet_draft",
        lambda document: dict(document),
    )

    confirmation = f"publish:{plan['plan_hash']}"
    first = orchestrator.publish_governed_evidence(
        str(tmp_path),
        plan=plan,
        confirmation=confirmation,
    )
    second = orchestrator.publish_governed_evidence(
        str(tmp_path),
        plan=plan,
        confirmation=confirmation,
    )
    assert first["delivery"]["stored"] is True
    assert second["delivery"]["stored"] is False
    assert first["delivery"]["entry_id"] == second["delivery"]["entry_id"]
    assert first["delivery"]["request_hash"] == second["delivery"]["request_hash"]

    outbox = tmp_path / ".apatch" / "governed_work" / "outbox"
    requests = [
        path
        for path in outbox.glob("*.json")
        if not path.name.endswith(".ack.json")
    ]
    assert len(requests) == 1
    stored = orchestrator.G._read_document(requests[0])
    assert stored["command"] == "evidence_admission"
    assert stored["tenant_id"] == change["tenant_id"]
    assert stored["project_group_id"] == change["project_group_id"]
    assert stored["client_id"] == config["client_id"]
    assert stored["endpoint"] == (
        f"/api/internal/project-groups/{change['project_group_id']}/"
        "governed-work/evidence-admissions"
    )
    assert stored["payload"] == {
        "evidence_bundle": bundle,
        "timesheet_draft": timesheet,
    }
    encoded = orchestrator.G.canonical_bytes(
        {"first": first, "second": second, "stored": stored}
    ).decode("utf-8")
    for forbidden in (
        "SOURCE-CANARY",
        "PATH-CANARY",
        "PROMPT-CANARY",
        "CREDENTIAL-CANARY",
        "TRANSCRIPT-CANARY",
        "GRANT-CANARY",
        "ECONOMIC-CANARY",
        "AVATAR-CANARY",
    ):
        assert forbidden not in encoded


def test_r5_offline_retry_requires_exact_receipt(
    monkeypatch, tmp_path: Path
) -> None:
    from apatch import governed_work_mcp as orchestrator
    from tests.test_governed_work_delivery import (
        Client,
        Provider,
        Response,
        config,
        make_binding,
        make_change,
        make_evidence,
        make_receipt,
        platform_sign,
    )

    actor, platform = Provider(), Provider()
    change = make_change(actor)
    binding = make_binding(change, platform)
    evidence, timesheet = make_evidence(actor, binding)
    active_config = config(platform, actor)
    plan = orchestrator._share_plan(
        config=active_config,
        change=change,
        bundle=evidence,
        timesheet=timesheet,
    )
    monkeypatch.setattr(
        orchestrator.D,
        "load_config",
        lambda _root, override=None: dict(override or active_config),
    )
    monkeypatch.setattr(
        orchestrator,
        "_publication_documents_for_plan",
        lambda _root, _plan: (change, evidence, timesheet),
    )
    published = orchestrator.publish_governed_evidence(
        str(tmp_path),
        plan=plan,
        confirmation=f"publish:{plan['plan_hash']}",
    )
    assert published["ok"] is True

    outbox_path = next(
        path
        for path in (tmp_path / ".apatch/governed_work/outbox").glob("*.json")
        if not path.name.endswith(".ack.json")
    )
    entry = orchestrator.G._read_document(outbox_path)
    frozen_request = outbox_path.read_bytes()
    clients = []

    network_failure = Client([])
    clients.append(network_failure)
    failed = orchestrator.D.sync_governed_work(
        str(tmp_path),
        config=active_config,
        request_key_provider=actor,
        http_client=network_failure,
    )
    assert failed["delivered"] == 0
    assert failed["pending"] == 1
    assert failed["errors"][0]["code"] == "DELIVERY_FAILED"
    assert outbox_path.read_bytes() == frozen_request

    no_receipt = Client([Response({})])
    clients.append(no_receipt)
    missing = orchestrator.D.sync_governed_work(
        str(tmp_path),
        config=active_config,
        request_key_provider=actor,
        http_client=no_receipt,
    )
    assert missing["delivered"] == 0
    assert missing["pending"] == 1
    assert missing["errors"][0]["code"] == "ACK_VALIDATION_FAILED"
    assert outbox_path.read_bytes() == frozen_request

    valid_receipt = make_receipt(entry, evidence, platform)
    wrong_body = {
        key: value
        for key, value in valid_receipt.items()
        if key != "signature"
    }
    wrong_body["request_hash"] = "sha256:" + "f" * 64
    wrong_receipt = platform_sign(
        wrong_body,
        platform,
        orchestrator.G.PLATFORM_EVIDENCE_ADMISSION_PURPOSE,
    )
    mismatch = Client([Response(wrong_receipt)])
    clients.append(mismatch)
    rejected = orchestrator.D.sync_governed_work(
        str(tmp_path),
        config=active_config,
        request_key_provider=actor,
        http_client=mismatch,
    )
    assert rejected["delivered"] == 0
    assert rejected["pending"] == 1
    assert rejected["errors"][0]["code"] == "ACK_VALIDATION_FAILED"
    assert outbox_path.read_bytes() == frozen_request

    accepted = Client([Response(valid_receipt)])
    clients.append(accepted)
    delivered = orchestrator.D.sync_governed_work(
        str(tmp_path),
        config=active_config,
        request_key_provider=actor,
        http_client=accepted,
    )
    assert delivered == {
        "ok": True,
        "status": "in_sync",
        "delivered": 1,
        "pending": 0,
        "errors": [],
    }
    assert outbox_path.read_bytes() == frozen_request
    request_bodies = [client.calls[0]["content"] for client in clients]
    assert all(body == request_bodies[0] for body in request_bodies)
    assert orchestrator.D.pending_count(str(tmp_path)) == 0


def test_r6_disconnect_fences_future_sharing_without_deleting_local_history(
    monkeypatch, tmp_path: Path
) -> None:
    from apatch import governed_work_mcp as orchestrator

    config = {
        "schema": "apatch.governed-work-config.v1",
        "platform_url": "https://clients.trust-chain.ai",
        "client_id": "member:developer",
        "service_request_key_id": "sha256:" + "1" * 64,
        "binding_authority_keys": {"platform:key": "public"},
    }
    change = {
        "tenant_id": "tenant-a",
        "project_group_id": "tcpg_" + "2" * 32,
    }
    timesheet = {
        "timesheet_id": "apts_" + "4" * 32,
        "claimed_active_seconds": 321,
    }
    bundle = {
        "bundle_id": "apweb_" + "3" * 32,
        "timesheet_ref": {
            "timesheet_id": timesheet["timesheet_id"],
            "timesheet_hash": orchestrator.G.document_hash(timesheet),
        },
    }
    plan = orchestrator._share_plan(
        config=config,
        change=change,
        bundle=bundle,
        timesheet=timesheet,
    )
    monkeypatch.setattr(orchestrator.D, "load_config", lambda _root: config)
    monkeypatch.setattr(
        orchestrator,
        "_publication_documents_for_plan",
        lambda _root, _plan: (change, bundle, timesheet),
    )
    monkeypatch.setattr(
        orchestrator,
        "_publication_documents",
        lambda _root, _binding: (change, bundle, timesheet),
    )
    monkeypatch.setattr(
        orchestrator.D,
        "validate_work_evidence_bundle",
        lambda document: dict(document),
    )
    monkeypatch.setattr(
        orchestrator.D,
        "validate_timesheet_draft",
        lambda document: dict(document),
    )
    published = orchestrator.publish_governed_evidence(
        str(tmp_path),
        plan=plan,
        confirmation=f"publish:{plan['plan_hash']}",
    )
    assert published["ok"] is True
    history = tmp_path / ".apatch" / "governed_work" / "changes" / "history.json"
    history.parent.mkdir(parents=True, exist_ok=True)
    history.write_text("local-history-must-survive", encoding="utf-8")
    outbox_path = next(
        path
        for path in (tmp_path / ".apatch/governed_work/outbox").glob("*.json")
        if not path.name.endswith(".ack.json")
    )
    frozen_history = history.read_bytes()
    frozen_outbox = outbox_path.read_bytes()

    first = orchestrator.disconnect_governed_work(
        str(tmp_path),
        disconnected_at="2026-09-10T00:00:00Z",
    )
    second = orchestrator.disconnect_governed_work(
        str(tmp_path),
        disconnected_at="2026-09-10T00:00:00Z",
    )
    assert first == {
        "ok": True,
        "operation": "disconnect",
        "state": "disconnected",
        "stored": True,
        "connection_generation": orchestrator.G.value_hash(config),
        "pending_fenced": 1,
    }
    assert second == {**first, "stored": False}
    assert history.read_bytes() == frozen_history
    assert outbox_path.read_bytes() == frozen_outbox

    preview = orchestrator.preview_governed_evidence_publication(
        str(tmp_path),
        binding_id="tcpsb_" + "5" * 32,
    )
    assert preview["ok"] is True
    blocked_publish = orchestrator.publish_governed_evidence(
        str(tmp_path),
        plan=plan,
        confirmation=f"publish:{plan['plan_hash']}",
    )
    assert blocked_publish["ok"] is False
    assert "disconnected" in blocked_publish["error"]

    def forbidden_sync(*_args, **_kwargs):
        raise AssertionError("disconnect must fail before HTTP sync")

    monkeypatch.setattr(orchestrator.D, "sync_governed_work", forbidden_sync)
    blocked_sync = orchestrator.sync_governed_work(str(tmp_path))
    assert blocked_sync["ok"] is False
    assert "disconnected" in blocked_sync["error"]
    assert history.read_bytes() == frozen_history
    assert outbox_path.read_bytes() == frozen_outbox

    reconnected = dict(config)
    reconnected["client_id"] = "member:developer:new-generation"
    monkeypatch.setattr(orchestrator.D, "load_config", lambda _root: reconnected)
    next_preview = orchestrator.preview_governed_evidence_publication(
        str(tmp_path),
        binding_id="tcpsb_" + "5" * 32,
    )
    assert next_preview["ok"] is True
    assert next_preview["plan"]["connection_generation"] != (
        plan["connection_generation"]
    )
    still_fenced = orchestrator.sync_governed_work(str(tmp_path))
    assert still_fenced["ok"] is False
    assert "pending evidence" in still_fenced["error"]
    assert history.read_bytes() == frozen_history
    assert outbox_path.read_bytes() == frozen_outbox


def test_r7_mcp_surface_and_compatibility(monkeypatch, tmp_path: Path) -> None:
    import pytest

    pytest.importorskip("mcp")
    from apatch import governed_work_mcp as orchestrator
    from apatch.mcp import server

    names = {
        "apatch_governed_work_preview_evidence",
        "apatch_governed_work_publish_evidence",
        "apatch_governed_work_disconnect",
    }
    assert names <= set(server.mcp._tool_manager._tools)
    build_schema = server.mcp._tool_manager._tools[
        "apatch_governed_work_build_evidence"
    ].parameters
    assert build_schema["properties"]["queue_for_admission"]["default"] is False
    calls = []

    def preview(target_dir, *, binding_id):
        calls.append(("preview", target_dir, {"binding_id": binding_id}))
        return {"ok": True, "operation": "preview_evidence_publication"}

    def publish(target_dir, *, plan, confirmation):
        calls.append(
            (
                "publish",
                target_dir,
                {"plan": plan, "confirmation": confirmation},
            )
        )
        return {"ok": True, "operation": "publish_evidence"}

    def disconnect(target_dir):
        calls.append(("disconnect", target_dir, {}))
        return {"ok": True, "operation": "disconnect"}

    monkeypatch.setattr(
        orchestrator,
        "preview_governed_evidence_publication",
        preview,
    )
    monkeypatch.setattr(orchestrator, "publish_governed_evidence", publish)
    monkeypatch.setattr(orchestrator, "disconnect_governed_work", disconnect)
    target = str(tmp_path)
    plan = {"plan_hash": "sha256:" + "1" * 64}
    preview_result = server.mcp._tool_manager._tools[
        "apatch_governed_work_preview_evidence"
    ].fn(binding_id="tcpsb_" + "2" * 32, target_dir=target)
    publish_result = server.mcp._tool_manager._tools[
        "apatch_governed_work_publish_evidence"
    ].fn(
        plan=plan,
        confirmation=f"publish:{plan['plan_hash']}",
        target_dir=target,
    )
    disconnect_result = server.mcp._tool_manager._tools[
        "apatch_governed_work_disconnect"
    ].fn(target_dir=target)
    assert preview_result["operation"] == "preview_evidence_publication"
    assert publish_result["operation"] == "publish_evidence"
    assert disconnect_result["operation"] == "disconnect"
    assert calls == [
        ("preview", target, {"binding_id": "tcpsb_" + "2" * 32}),
        (
            "publish",
            target,
            {
                "plan": plan,
                "confirmation": f"publish:{plan['plan_hash']}",
            },
        ),
        ("disconnect", target, {}),
    ]

    guide = (ROOT / "docs/governed-work-trustchain.md").read_text(
        encoding="utf-8"
    )
    workflow = guide.split("## Governed workflow", 1)[1].split(
        "## Recovery", 1
    )[0]
    evidence_flow = workflow[
        workflow.index("5. `apatch_governed_work_build_evidence`") :
    ]
    positions = [
        evidence_flow.index("apatch_governed_work_build_evidence"),
        evidence_flow.index("apatch_governed_work_preview_evidence"),
        evidence_flow.index("apatch_governed_work_publish_evidence"),
        evidence_flow.index("apatch_governed_work_sync"),
        evidence_flow.index("apatch_governed_work_disconnect"),
    ]
    assert positions == sorted(positions)
    assert "default is local-only" in evidence_flow
    assert "exact content-free plan" in evidence_flow
    assert "fail before the outbox" in evidence_flow
    assert "stop before network access" in evidence_flow


def test_r8_cowork_consumer_round_trip(monkeypatch, tmp_path: Path) -> None:
    from apatch import governed_work_mcp as orchestrator
    from tests.test_governed_work_delivery import (
        Client,
        Provider,
        Response,
        config,
        make_binding,
        make_change,
        make_evidence,
        make_receipt,
    )

    actor, platform = Provider(), Provider()
    change = make_change(actor)
    binding = make_binding(change, platform)
    evidence, timesheet = make_evidence(actor, binding)
    active_config = config(platform, actor)
    orchestrator.D.write_config(str(tmp_path), active_config)
    root = orchestrator.G.governed_work_root(str(tmp_path))
    artifact_paths = [
        root / "changes" / f"{change['change_id']}.json",
        root / "bindings" / f"{binding['binding_id']}.json",
        root / "timesheets" / f"{timesheet['timesheet_id']}.json",
        root / "evidence" / f"{evidence['bundle_id']}.json",
    ]
    for path, document in zip(
        artifact_paths,
        (change, binding, timesheet, evidence),
    ):
        orchestrator.G._atomic_json(path, document)
    frozen = {str(path): path.read_bytes() for path in artifact_paths}
    avatar_outbox = tmp_path / "avatar-evidence-outbox"
    monkeypatch.setenv("APATCH_AVATAR_EVIDENCE_OUTBOX", str(avatar_outbox))
    monkeypatch.setattr(
        orchestrator.G,
        "_load_local_provider",
        lambda _root, key_provider=None: key_provider or actor,
    )
    monkeypatch.setattr(
        orchestrator.G,
        "build_workspace_evidence",
        lambda *_args, **_kwargs: {
            "change_id": change["change_id"],
            "evidence_bundle": evidence,
            "evidence_bundle_hash": orchestrator.G.document_hash(evidence),
            "timesheet": timesheet,
            "timesheet_hash": orchestrator.G.document_hash(timesheet),
        },
    )

    built = orchestrator.build_governed_evidence(
        str(tmp_path),
        binding_id=binding["binding_id"],
    )
    assert built["evidence_admission_delivery"] == {
        "status": "not_requested"
    }
    assert orchestrator.D.pending_count(str(tmp_path)) == 0

    preview = orchestrator.preview_governed_evidence_publication(
        str(tmp_path),
        binding_id=binding["binding_id"],
    )
    assert preview["ok"] is True
    assert {str(path): path.read_bytes() for path in artifact_paths} == frozen
    plan = preview["plan"]
    published = orchestrator.publish_governed_evidence(
        str(tmp_path),
        plan=plan,
        confirmation=f"publish:{plan['plan_hash']}",
    )
    assert published["ok"] is True
    assert orchestrator.D.pending_count(str(tmp_path)) == 1

    offline = Client([])
    retry = orchestrator.sync_governed_work(
        str(tmp_path),
        http_client=offline,
        request_key_provider=actor,
    )
    assert retry["status"] == "delivery_incomplete"
    assert retry["pending"] == 1
    outbox_path = next(
        path
        for path in (root / "outbox").glob("*.json")
        if not path.name.endswith(".ack.json")
    )
    entry = orchestrator.G._read_document(outbox_path)
    receipt = make_receipt(entry, evidence, platform)
    accepted = Client([Response(receipt)])
    delivered = orchestrator.sync_governed_work(
        str(tmp_path),
        http_client=accepted,
        request_key_provider=actor,
    )
    assert delivered["status"] == "in_sync"
    assert delivered["delivered"] == 1
    assert delivered["pending"] == 0
    assert offline.calls[0]["content"] == accepted.calls[0]["content"]

    disconnected = orchestrator.disconnect_governed_work(
        str(tmp_path),
        disconnected_at="2026-09-10T00:00:00Z",
    )
    assert disconnected["ok"] is True
    assert disconnected["pending_fenced"] == 0
    after_disconnect = orchestrator.preview_governed_evidence_publication(
        str(tmp_path),
        binding_id=binding["binding_id"],
    )
    assert after_disconnect["plan"] == plan
    assert {str(path): path.read_bytes() for path in artifact_paths} == frozen
    assert not avatar_outbox.exists()
    assert not (tmp_path / ".apatch" / "avatar").exists()
