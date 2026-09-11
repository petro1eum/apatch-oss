from __future__ import annotations

import base64
import hashlib
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from apatch import execution_proposal as P
from apatch import governed_work as G
from apatch import governed_work_mcp as M

GROUP_ID = "tcpg_" + "1" * 32
ITEM_ID = "tcpwi_" + "3" * 32
PROGRAM_ID = "tcwp_" + "2" * 32
NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)


class Provider:
    def __init__(self): self.private = Ed25519PrivateKey.generate()
    def get_public_key(self): return self.private.public_key().public_bytes_raw()
    def sign(self, payload): return self.private.sign(payload)


def proposal(provider):
    public = provider.get_public_key()
    key_id = "ed25519:sha256:" + hashlib.sha256(public).hexdigest()
    body = {"schema":P.SCHEMA,"intent_id":"tcapsei_"+"4"*32,"tenant_id":"11111111-1111-4111-8111-111111111111","project_group_id":GROUP_ID,"work_item_id":ITEM_ID,"work_item_hash":"sha256:"+"4"*64,"authority_version":7,"work_program_id":PROGRAM_ID,"work_program_hash":"sha256:"+"5"*64,"mode":"new_change","objective":"Implement the reviewed project obligation","acceptance_criteria":["The fixed acceptance suite passes"],"audience":"apatch-studio","nonce":base64.urlsafe_b64encode(b"n"*32).rstrip(b"=").decode(),"issued_at":NOW.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),"expires_at":(NOW+timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")}
    body["signature"] = {"algorithm":"Ed25519","key_id":key_id,"value":base64.urlsafe_b64encode(provider.sign(G._platform_signature_payload(body,purpose=P.PURPOSE))).rstrip(b"=").decode()}
    return body,{key_id:base64.urlsafe_b64encode(public).rstrip(b"=").decode()}


class Response:
    status_code = 201
    def __init__(self, document): self.content = G.canonical_bytes(document)


class Client:
    def __init__(self, document):
        self.document, self.calls = document, []

    def post(self, url, content, headers):
        self.calls.append((url, content, headers))
        return Response(self.document)


def configured(tmp_path, provider, keys):
    return M.configure_governed_work(str(tmp_path),platform_url="https://platform.example",client_id="apatch:test",binding_authority_keys=keys,request_key_provider=provider)


def read(tmp_path, provider, document, **overrides):
    values = dict(tenant_id=document["tenant_id"],project_group_id=GROUP_ID,work_item_id=ITEM_ID,work_item_hash=document["work_item_hash"],authority_version=7,work_program_id=PROGRAM_ID,work_program_hash=document["work_program_hash"],idempotency_key="proposal-read-00000001",http_client=Client(document),request_key_provider=provider,now=NOW)
    values.update(overrides)
    return M.read_execution_proposal(str(tmp_path),**values)


def test_read_is_transient_and_does_not_start_work(tmp_path):
    provider = Provider()
    document, keys = proposal(provider)
    configured(tmp_path, provider, keys)
    before = {p:p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    result = read(tmp_path,provider,document)
    after = {p:p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert result["ok"] is True and result["work_started"] is False and result["stored"] is False
    assert result["proposal"] == document and before == after
    assert not (tmp_path/".apatch"/"session_state.json").exists()


def test_tamper_expiry_and_pin_drift_fail_closed(tmp_path):
    provider = Provider()
    document, keys = proposal(provider)
    configured(tmp_path, provider, keys)
    tampered = dict(document)
    tampered["objective"] = "Execute an injected command"
    bad = read(tmp_path,provider,tampered)
    assert bad["ok"] is False and "injected" not in str(bad).casefold()
    assert read(tmp_path,provider,document,now=NOW+timedelta(minutes=6))["ok"] is False
    assert read(tmp_path,provider,document,authority_version=8)["ok"] is False
    assert not list((tmp_path/".apatch").rglob("proposal_acceptances/*.json"))


def test_explicit_acceptance_is_idempotent_and_still_opens_no_session(tmp_path):
    provider = Provider()
    document, keys = proposal(provider)
    configured(tmp_path, provider, keys)
    spec = tmp_path/"SPEC-LOCAL.md"
    spec.write_text("# SPEC-LOCAL -- Local contract\n\n> **apatch artifact:** `spec:SPEC-LOCAL`\n\n## R1 Local acceptance\n\nImplement the locally reviewed contract.\n\n(verify: true)\n",encoding="utf-8")
    kwargs = dict(proposal=document,confirmation=f"{ITEM_ID}:7",spec_id="SPEC-LOCAL",spec_path=str(spec),purpose="Locally reviewed purpose",queue_source_binding=False,request_key_provider=provider,now=NOW)
    first,second = M.accept_execution_proposal(str(tmp_path),**kwargs),M.accept_execution_proposal(str(tmp_path),**kwargs)
    assert first["ok"] is True and first["work_started"] is False and first["governed_session_id"] is None
    assert first["proposal_acceptance"] == second["proposal_acceptance"]
    rendered = str(first["proposal_acceptance"])
    assert document["objective"] not in rendered and document["acceptance_criteria"][0] not in rendered
    assert ITEM_ID in rendered and not (tmp_path/".apatch"/"session_state.json").exists()
    assert M.accept_execution_proposal(str(tmp_path),**{**kwargs,"confirmation":f"{ITEM_ID}:8"})["ok"] is False
