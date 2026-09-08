# SPEC-DESIGN-PARTNER-1 — Design Partner Program playbook (AR-7)

> **Status:** Draft v1 · **Owner:** apatch product  
> **apatch artifact:** `spec:SPEC-DESIGN-PARTNER-1`  
> **Anchors:** [RFP-021 §AR-7](../RFP-021-agent-reliability-design-partner.md) · depends on attested AR-2–AR-6 specs

## 0. Motivation

External design partners need a **single English playbook**: setup, agent contract,
success criteria, escalation, and the G1–G7 L1 checklist — without reading the full
apatch RFP chain.

Non-goals: marketing site; PyPI listing; reducing MCP tool count.

## R1 playbook document exists with prerequisites

`docs/design-partner-playbook.md` includes §2 Prerequisites (Python 3.11+, git, MCP, TrustChain).

(verify: python3 -m pytest tests/test_design_partner_playbook.py::test_playbook_has_prerequisites -q)

## R2 one-time setup section

Playbook documents `init-consumer --with-sandbox --with-enforcement --with-mcp` and `apatch mcp sync`.

(verify: python3 -m pytest tests/test_design_partner_playbook.py::test_playbook_has_setup -q)

## R3 agent contract and governed cycle

Playbook documents `apatch_doctor` first call, governed cycle, and `apatch_spec_run` for whole specs.

(verify: python3 -m pytest tests/test_design_partner_playbook.py::test_playbook_has_agent_contract -q)

## R4 L1 checklist G1–G7

Playbook §9 table lists all seven gates with verification hints (RFP-021 §10).

(verify: python3 -m pytest tests/test_design_partner_playbook.py::test_playbook_has_l1_gates -q)

## R5 escalation fix_forward vs rollback

Playbook §7 documents `fix_forward`, `rollback`, `resume_session` (links AR-3).

(verify: python3 -m pytest tests/test_design_partner_playbook.py::test_playbook_has_escalation -q)

## R6 parallel agents and lanes

Playbook §8 references worktree + lane per agent (RFP-019).

(verify: python3 -m pytest tests/test_design_partner_playbook.py::test_playbook_has_parallel_agents -q)

## R7 init-consumer deploys playbook

`apatch init-consumer` copies `docs/design-partner-playbook.md` into consumer `docs/` when absent.

(verify: python3 -m pytest tests/test_design_partner_playbook.py::test_init_consumer_copies_playbook -q)

## R8 docs index links playbook

`docs/README.md` links the playbook under RFP-021 / design partner row.

(verify: python3 -m pytest tests/test_design_partner_playbook.py::test_docs_readme_links_playbook -q)
