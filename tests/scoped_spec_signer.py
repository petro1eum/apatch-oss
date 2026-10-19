"""Real scoped Ed25519 ledger, no fake commit or persisted receipt."""
import base64,hashlib,json,os
from pathlib import Path
import pytest
from apatch.trustchain_helper import TrustChainHelper as ActualTrustChainHelper
TARGETS={'tests/test_spec_run.py::test_spec_run_completes_when_another_lane_is_active', 'tests/test_spec_run.py::test_spec_run_verify_precheck_does_not_skip_when_needles_set', 'tests/test_spec_run.py::test_spec_run_executes_two_rk_chunked', 'tests/test_spec_run_multi.py::test_run_multi_safe_order', 'tests/test_spec_run.py::test_spec_run_executes_two_rk_sequence', 'tests/test_spec_run.py::test_spec_run_reruns_attested_requirement_when_skip_is_false', 'tests/test_spec_run.py::test_spec_run_inline_requirements_one_call', 'tests/test_spec_run.py::test_spec_run_blocked_and_resume', 'tests/test_spec_run_allow_partial.py::test_spec_run_allow_partial_runs_subset', 'tests/test_spec_run.py::test_spec_run_uses_manifest_verify_override', 'tests/test_spec_run_multi.py::test_run_multi_explicit_requirements_are_bounded_work_lists', 'tests/test_spec_run_multi.py::test_multi_failure_restores_all_completed_checkpoints', 'tests/test_spec_run.py::test_spec_run_attested_explicit_skip_and_satisfied_noop'}
CURRENT=None

def current_helper():
    assert CURRENT is not None,'real fixture not initialized'
    return CURRENT

@pytest.fixture(autouse=True)
def scoped_real_signer(tmp_path,monkeypatch,request):
    global CURRENT
    normalized=Path(request.node.path).name+'::'+request.node.name
    if 'tests/'+normalized not in TARGETS:
        yield
        return
    from tests.test_trust_anchor import _write_enrolled_key
    from apatch.trust_identity import _PemKeyProvider
    from trustchain import TrustChain,TrustChainConfig,TrustChainVerifier
    from trustchain.v2.chain_store import verify_record_signature
    for key in list(os.environ):
        if key.startswith(('APATCH_','TRUSTCHAIN_')):monkeypatch.delenv(key,raising=False)
    root=tmp_path.resolve();(root/'.apatch').mkdir(exist_ok=True)
    key,pub=_write_enrolled_key(root);agent='disposable-spec-real-signer'
    (root/'.apatch/agent-identity.json').write_text(json.dumps({'agent_id':agent,'key_backend':'pem','key':key,'public_key':pub}))
    tc=TrustChain(TrustChainConfig(enable_chain=True,chain_storage='file',chain_dir=str(root/'.trustchain'),key_provider=_PemKeyProvider(key,agent),enable_pki=False))
    tc.sign('fixture_genesis',{'fictional':True,'fixture':'SPEC-run-positive-ledger'})
    helper=ActualTrustChainHelper(str(root),auto_init=False)
    assert Path(helper.workspace_root).resolve()==root
    assert Path(helper.trustchain_dir).resolve()==root/'.trustchain'
    CURRENT=helper
    def factory(*args,**kwargs):return helper
    for name in ('_FakeTrustChain','_RecordingTrustChain'):
        if hasattr(request.module,name):monkeypatch.setattr(request.module,name,factory)
    # request.module includes the allow_partial module's directly imported alias.
    try:yield
    finally:
        CURRENT=None
        verifier=TrustChainVerifier(pub,agent,max_age_seconds=None)
        rows=[]
        for p in sorted((root/'.trustchain/objects').glob('op_*.json'),key=lambda p:int(p.stem[3:])):
            record=json.loads(p.read_bytes());record=record.get('value',record)
            assert verify_record_signature(record,verifier),p
            rows.append({'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'tool':record.get('tool'),'action':(record.get('data')or{}).get('action'),'governed_session_id':(record.get('data')or{}).get('governed_session_id')})
        # Qualification requires real durable effects, not fake return values.
        assert any(r['action']=='effect_prepared' for r in rows),rows
        out=Path(os.environ.get('SPEC_REAL_EVIDENCE',str(root/'real-signer-evidence')));out.mkdir(exist_ok=True)
        (out/(hashlib.sha256(request.node.nodeid.encode()).hexdigest()+'.json')).write_text(json.dumps({'node':request.node.nodeid,'workspace':str(root),'public_key':pub,'verified_signed_records':rows,'helper_type':type(helper).__module__+'.'+type(helper).__name__},indent=2)+'\n')
