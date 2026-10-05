# SPEC-OSS-CONTRACT-INTAKE-EXECUTION-1 — SPEC-OSS-CONTRACT-INTAKE-EXECUTION-1 (scaffolded from RFP-050-OSS-CONTRACT-INTAKE-PORT)

> **apatch artifact:** `spec:SPEC-OSS-CONTRACT-INTAKE-EXECUTION-1`  
> **Anchors:** RFP-050-OSS-CONTRACT-INTAKE-PORT  
> **ownership mode:** strict
> Approved transport of the frozen PORT-1..6 package. Source checks and installed-pair release qualification remain distinct.

## R0 RFP traceability gate (meta)

owns: docs/specs/SPEC-OSS-CONTRACT-INTAKE-EXECUTION-1.md, docs/RFP-050-OSS-CONTRACT-INTAKE-PORT.md, tests/portable_intake/test_portable_intake.py, tests/portable_intake/native_intake_fixture.py, tests/portable_intake/helper-pins.json, apatch/sdd_preparation_io.py, apatch/strict_existing_signer.py, apatch/sdd_contract_intake.py

| RFP id | SPEC Rk | Disposition |
|--------|---------|-------------|
| PORT-1 | R1 | covered |
| PORT-2 | R2 | covered |
| PORT-3 | R3 | covered |
| PORT-4 | R4 | covered |
| PORT-5 | R5 | covered |
| PORT-6 | R6 | covered |

(verify: python3 -m apatch.cli spec lint --spec SPEC-OSS-CONTRACT-INTAKE-EXECUTION-1 --json)

## R1 Fixed IDs and exact contents survive read-only preparation and real n…

owns: apatch/sdd_preparation_io.py, apatch/strict_existing_signer.py, apatch/sdd_contract_intake.py, apatch/sdd_integrity.py


Fixed IDs and exact contents survive read-only preparation and real native create/replace/approval/restart replay. Exactly two genuine verified receipts appear; no key/profile/frozen input changes or execution authority occur.

(verify: python3 -m pytest -q tests/portable_intake/test_portable_intake.py tests/test_sdd_integrity.py -k 'not public_studio and not installed_pair')

## R2 Foreign/uncertified owner, changed review/snapshot/profile/frozen jud…

owns: apatch/sdd_contract_intake.py


Foreign/uncertified owner, changed review/snapshot/profile/frozen judge fail before any signature or installation, after a successful preparation control.

(verify: python3 -m pytest -q tests/portable_intake/test_portable_intake.py -k 'owner_denial or snapshot_and_active')

## R3 Symlink, hardlink, traversal and runtime-source targets are refused w…

owns: apatch/sdd_contract_intake.py


Symlink, hardlink, traversal and runtime-source targets are refused without side effects; unknown pending transaction fences implementation without deleting evidence.

(verify: python3 -m pytest -q tests/portable_intake/test_portable_intake.py -k 'file_boundary or unknown_pending')

## R4 The functional generic transaction works while every private migratio…

owns: apatch/sdd_preparation_io.py, apatch/sdd_contract_intake.py


The functional generic transaction works while every private migration module is absent and unloaded. Eight extracted generic function bodies match their prequalified byte fingerprints.

(verify: python3 -m pytest -q tests/portable_intake/test_portable_intake.py -k 'generic_runtime or extracted_generic')

## R5 Current Studio adds GET/POST review and POST approve inside the exist…

owns: tests/portable_intake/test_portable_intake.py


Current Studio adds GET/POST review and POST approve inside the existing security boundary. Missing session yields exactly 401, missing origin exactly 403; both leave the workspace and ledger unchanged. The frozen original 23 API checks qualify successful owner approval and replay.

(verify: python3 -I -m pytest -q tests/portable_intake/test_portable_intake.py -k public_studio)

## R6 Exact built Core 0.8.50 and Studio 0.1.5 wheels pass the entire suite…

owns: tests/portable_intake/test_portable_intake.py


Exact built Core 0.8.50 and Studio 0.1.5 wheels pass the entire suite in an isolated interpreter outside both checkouts, with package files only in that environment's site-packages. Existing publication, standalone/Cowork, signer-isolation and artifact gates stay mandatory.

(verify: python3 -I -m pytest -q tests/portable_intake/test_portable_intake.py)
