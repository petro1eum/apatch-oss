# SPEC-INTERFERENCE-4 — L0 domain tag routing (RFP-014 Phase 4)

> **apatch artifact:** `spec:SPEC-INTERFERENCE-4`
> **Anchors:** [RFP-014 Phase 4](../RFP-014-spec-interference-detection.md), [SPEC-INTERFERENCE-1](./SPEC-INTERFERENCE-1.md)

## 0. Motivation

Phase 4 adds **L0 tag pre-filter** over L1/L2 interference: `domain:ux` vs `domain:backend`
on overlapping paths must run full analysis; orthogonal domains with no shared registry
paths short-circuit expensive checks.

## R1 L0 pair routing module

`apatch/spec_interference_l0.py` implements `l0_pair_routing`, `build_l0_routing_matrix`,
`l1_planned_file_overlaps`, and registry tag loading.

(verify: python3 -m pytest tests/test_spec_interference_l0.py::test_l0_pair_routing_orthogonal_domains tests/test_spec_interference_l0.py::test_l0_pair_routing_ux_backend_shared_path -q)

## R2 L0 wired into interference report

`spec_interference_from_data` applies L0 gates; report includes `l0_routing`;
`update_spec_registry` persists optional `tags[]`.

(verify: python3 -m pytest tests/test_spec_interference_l0.py::test_l0_planned_only_validity -q)

## R3 L0 stress fixture orthogonal skip

`tests/fixtures/l0_domain/` registry entries with `domain:ux` vs `domain:backend` on
disjoint paths; interference returns zero conflicts and L0 action `skip`.

(verify: python3 -m pytest tests/test_spec_interference_l0.py::test_l0_fixture_orthogonal_no_conflicts -q)

## R4 Cross-domain shared-path write-write

Same file + `domain:ux` + `domain:backend` → L0 `full`, L1 overlap + L2 write_write.

(verify: python3 -m pytest tests/test_spec_interference_l0.py::test_l0_fixture_ux_backend_write_write_on_shared_file -q)