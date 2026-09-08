"""SPEC-CONCEPT-COMPILE-1 — concept/claim fence compiler (RFP-034 §B.1). No-mock."""
import json

from apatch.concept_compile import compile_concept_graph, write_concept_map

DOC = """# Payments

<!-- @cid:cpt_gateway -->
```concept
name: Gateway
aliases: [payment gateway]
realized_by: src/payments/gateway.py:Gateway
relations:
  - {rel: constrains, to: cpt_refund, invariant: REFUND-VIA-GATEWAY}
invariants:
  - {kind: arch_rule, ref: NO-PAYMENT-BYPASS}
```
Single entry point for payments.

<!-- @cid:cpt_refund -->
```concept
name: Refund
realized_by: src/payments/refund.py:RefundService
```
Refund must go through the gateway.

<!-- @sid:claim_1 -->
```claim
type: decision
about: [cpt_gateway]
edges: {rejects: claim_9}
verify: {arch_rule: NO-PAYMENT-BYPASS}
```
Gateway is the single entry point.
"""


def test_r1_parse_concept_node():
    """R1: a `concept` fence yields a node with cid/name/aliases/definition/realized_by/
    relations/invariants."""
    n = compile_concept_graph(DOC)["concepts"]["cpt_gateway"]
    assert n["name"] == "Gateway"
    assert n["aliases"] == ["payment gateway"]
    assert n["realized_by"] == ["src/payments/gateway.py:Gateway"]
    assert n["relations"][0]["to"] == "cpt_refund"
    assert n["invariants"][0]["ref"] == "NO-PAYMENT-BYPASS"
    assert "Single entry point" in n["definition"]


def test_r2_parse_claim():
    """R2: a `claim` fence yields sid/type/about/edges/verify."""
    c = compile_concept_graph(DOC)["claims"]["claim_1"]
    assert c["type"] == "decision"
    assert c["about"] == ["cpt_gateway"]
    assert c["edges"]["rejects"] == "claim_9"
    assert c["verify"]["arch_rule"] == "NO-PAYMENT-BYPASS"


def test_r3_dangling_reference_is_error():
    """R3: a relation.to or about pointing at an undefined concept is an error (not silent)."""
    g = compile_concept_graph(DOC.replace("to: cpt_refund", "to: cpt_missing"))
    assert any("unknown concept 'cpt_missing'" in e for e in g["errors"]), g["errors"]
    g2 = compile_concept_graph(DOC.replace("about: [cpt_gateway]", "about: [cpt_ghost]"))
    assert any("cpt_ghost" in e for e in g2["errors"]), g2["errors"]


def test_r4_duplicate_id_is_error():
    """R4: a duplicate cid (or sid) is reported, never silently overwritten."""
    g = compile_concept_graph(DOC + DOC)
    assert any("duplicate cid" in e for e in g["errors"]), g["errors"]


def test_r5_clean_graph_and_write(tmp_path):
    """R5: a clean doc compiles to {schema_version, concepts, claims, errors:[]} and
    write_concept_map persists concept_map.json."""
    g = compile_concept_graph(DOC)
    assert g["schema_version"] == 1
    assert set(g["concepts"]) == {"cpt_gateway", "cpt_refund"}
    assert g["errors"] == []
    written = write_concept_map(DOC, out_dir=str(tmp_path))
    data = json.load(open(written["_path"], encoding="utf-8"))
    assert data["concepts"]["cpt_gateway"]["name"] == "Gateway"