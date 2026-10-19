"""Additive release checks; historical v3 positive and negative judges are preserved."""
import ast,copy,hashlib,json,shutil
from pathlib import Path
import pytest
if json.loads((Path(__file__).resolve().parents[1]/'PUBLIC-SOURCE-MANIFEST.json').read_text())['schema']=='apatch.public-source.v5':
 from scripts.check_public_stabilization_v5 import validate,EXPECTED_RUNTIME
else:
 from scripts.check_public_stabilization import validate,EXPECTED_RUNTIME
ROOT=Path(__file__).resolve().parents[1]

def manifest():return json.loads((ROOT/'PUBLIC-SOURCE-MANIFEST.json').read_text())

def test_stabilization_matches_official_baseline_and_exact_export():
 assert validate(manifest(),ROOT)

@pytest.mark.parametrize('tamper',['version','baseline','historical_commit','historical_hash','historical_inventory','added_private','missing_delta','extra_delta','duplicate_delta','runtime_pin','runtime_count','source_bytes','source_mode','unsafe_path','claim_private_history'])
def test_stabilization_rejects_unreviewed_manifest(tamper):
 m=copy.deepcopy(manifest())
 if tamper=='version':m['version']='0.8.50'
 elif tamper=='baseline':m['baseline_commit']='0'*40
 elif tamper=='historical_commit':m['historical_public_source']['public_commit']='0'*40
 elif tamper=='historical_hash':m['historical_public_source']['manifest_sha256']='0'*64
 elif tamper=='historical_inventory':m['historical_public_source']['manifest']['files'].pop()
 elif tamper=='added_private':m['files'].append({**m['files'][0],'path':'apatch/sdd_judge_amendment.py'})
 elif tamper=='missing_delta':m['release_delta'].pop()
 elif tamper=='extra_delta':m['release_delta'].append({'path':'apatch/cli.py','before_sha256':'0'*64,'after_sha256':'0'*64})
 elif tamper=='duplicate_delta':m['release_delta'].append(m['release_delta'][0])
 elif tamper=='runtime_pin':next(r for r in m['files']if r['path']=='apatch/trustchain_helper.py')['sha256']='0'*64
 elif tamper=='runtime_count':m['runtime_files_total']+=1
 elif tamper=='source_bytes':next(r for r in m['files']if r['path']=='pyproject.toml')['bytes']+=1
 elif tamper=='source_mode':next(r for r in m['files']if r['path']=='pyproject.toml')['mode']='0o755'
 elif tamper=='unsafe_path':m['files'][0]['path']='../outside.py'
 elif tamper=='claim_private_history':m['private_git_history_included']=True
 with pytest.raises(AssertionError):validate(m,ROOT)

@pytest.mark.parametrize('tamper',['unlisted_runtime','symlink','hardlink','changed_bytes'])
def test_stabilization_rejects_actual_filesystem_tampering(tmp_path,tamper):
 root=tmp_path/'candidate';shutil.copytree(ROOT,root,ignore=shutil.ignore_patterns('.git','__pycache__','.pytest_cache'))
 (root/'.git').symlink_to(ROOT/'.git',target_is_directory=True)
 target=root/'apatch/agent_guidance.py'
 if tamper=='unlisted_runtime':(root/'apatch/unreviewed.py').write_text('print("unreviewed")\n')
 elif tamper=='symlink':
  external=tmp_path/'external.py';external.write_bytes(target.read_bytes());target.unlink();target.symlink_to(external)
 elif tamper=='hardlink':
  import os
  os.link(target,tmp_path/'linked.py')
 else:target.write_bytes(target.read_bytes()+b'\n# unreviewed mutation\n')
 with pytest.raises(AssertionError):validate(manifest(),root)

HISTORICAL_FUNCTION_SHA256 = {'_checker': '370083081db7084b7f26bb41d8d5cb7e6dd77ea5bf63d6aa94b99def252fbc73', '_git_at': 'ab2fa2cf88b76e03cd109383f7eae32be2535764d884741dbc62294562e19eaf', '_validate_intake_release_manifest': '9be3326a512f1579c04b9ce635a436359af8049be9a74f76587b59ccddc0bbc4', 'test_selected_public_source_matches_recorded_manifest': '59764d410fc86944f62c26bf2e68f152fbc9b47d0fe3c6af59985c651c7369f0', 'test_public_release_inventory_rejects_forged_history_or_scope': '0fb423de243cee8ed6f6642c7f8e2da704e841cbeed245984089908f89dcc6ad', 'test_private_components_and_operational_state_are_not_selected': 'ac516f6c21f78ee482754f7e471f70ab8fd42f00f0480994e215802b1f25b208', 'test_public_documentation_targets_and_anchors_resolve': '55af2df4e789c092eb644e358b09c24cf80b2e20bccb84801377a20b36baa909', 'test_pypi_description_has_no_relative_or_private_links': '1e700020b13c9e3309d5479383fc59acf09ef63cdcf1b18ca0f1e0e02404bbaa', 'test_docs_checker_rejects_missing_target': '61054ef541774c1f4af701119e5630e321020916cacedf580b1750a1a3ab9a46', 'test_docs_checker_rejects_missing_anchor': '3150bb638f7b8815aa0c8d72d01a645addde16da6e4d510a014c587e033d7d5b', 'test_docs_checker_rejects_private_repository_link': '1899e86600c0fea08d929f9c84ef88af4f08202a2088280f15922db6efa5b85c', 'test_docs_checker_distinguishes_approved_public_repository': '055a95c246328089354574a105e33b22275766da33cebd6268532fa8842a7530', 'intake_inventory_fixture': 'bbee5e2f4f8bd83f211740c0245df69c500a5792feb1fa83fb1b392bbfa88f2f', 'test_intake_release_inventory_accepts_exact_public_transfer': 'a415ae949de54918c71b55ecd7fcd40463c0689c982caf4ff87809b84656c449', 'test_historical_0_8_49_inventory_still_uses_original_validator': '8ba02db0ab38615de7ab6087e7f9ddb9759e1f9411b8bd72ae2ece75470ccc55', 'test_intake_release_inventory_rejects_one_manifest_tamper': 'd8e8406698d39045d55aaefc4b4d8409f6218ebf223c58e65416dd495301651a', 'test_intake_release_inventory_rejects_actual_filesystem_tamper': 'f582dbf58e770d42cc4c5bfa8ea2b3b012b4a47402e262a616b562f284d96b6d', 'test_intake_release_inventory_requires_actual_public_ancestor': '2d70ba13d9c4d7bee6a59306301b3169cfce551b81b5fda1d0d505baa0a2e9dd'}

def test_all_historical_v3_validator_and_test_bodies_are_preserved():
 source=(ROOT/'tests/test_public_source_boundary.py').read_text();lines=source.splitlines();nodes=ast.parse(source).body
 actual={n.name:hashlib.sha256('\n'.join(lines[n.lineno-1:n.end_lineno]).encode()).hexdigest()for n in nodes if isinstance(n,(ast.FunctionDef,ast.ClassDef))and n.name!='_validate_release_manifest'}
 assert actual==HISTORICAL_FUNCTION_SHA256
