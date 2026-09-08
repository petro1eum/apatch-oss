"""SPEC-CONCEPT-DEDUP-1 — raise merge candidates, never auto-merge (RFP-034 §B.2/§3.3)."""
from apatch.concept_compile import compile_concept_graph, dedup_candidates

# Canon §3.3 example: is "payment gateway" one concept or two? The machine doesn't know.
TWO_NAMES = """<!-- @cid:cpt_gateway -->
```concept
name: Платёжный шлюз
aliases: [gateway, payment gateway]
realized_by: src/pay/gateway.py:Gateway
```
Точка входа.

<!-- @cid:cpt_processor -->
```concept
name: Процессор транзакций
aliases: [payment gateway]
realized_by: src/pay/processor.py:Processor
```
Обработчик.
"""

SHARED_RB = """<!-- @cid:cpt_a -->
```concept
name: A
realized_by: src/x.py:Thing
```
A.

<!-- @cid:cpt_b -->
```concept
name: B
realized_by: src/x.py:Thing
```
B.
"""

DISTINCT = """<!-- @cid:cpt_one -->
```concept
name: One
realized_by: a.py:One
```
One.

<!-- @cid:cpt_two -->
```concept
name: Two
realized_by: b.py:Two
```
Two.
"""


def test_r1_shared_name_raises_candidate():
    """R1: two concepts sharing a name/alias are raised as a merge candidate with
    reason + evidence (the canon's gateway/processor case)."""
    cands = dedup_candidates(compile_concept_graph(TWO_NAMES))
    assert len(cands) == 1, cands
    c = cands[0]
    assert {c["a"], c["b"]} == {"cpt_gateway", "cpt_processor"}
    assert c["reason"] == "shared name/alias"
    assert "payment gateway" in c["evidence"]


def test_r2_shared_realized_by_raises_candidate():
    """R2: two concepts pointing at the same realized_by anchor are raised as a candidate."""
    cands = dedup_candidates(compile_concept_graph(SHARED_RB))
    assert any(c["reason"] == "shared realized_by" and "src/x.py:Thing" in c["evidence"]
               for c in cands), cands


def test_r3_no_auto_merge():
    """R3: human-in-loop boundary — distinct concepts raise nothing, and when candidates
    DO exist the graph is left untouched (both stay separate; no same_as written)."""
    assert dedup_candidates(compile_concept_graph(DISTINCT)) == []
    g = compile_concept_graph(TWO_NAMES)
    assert dedup_candidates(g)                                # candidate(s) raised
    assert {"cpt_gateway", "cpt_processor"} <= set(g["concepts"])  # both still separate