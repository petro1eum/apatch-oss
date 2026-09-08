# SPEC-REMOTE-SSH-VERIFY-1 — Remote verify, diagnostics, and resume

> **Status:** Draft v1 · **Owner:** apatch core  
> **apatch artifact:** `spec:SPEC-REMOTE-SSH-VERIFY-1`  
> **Anchors:** [RFP-029](../RFP-029-remote-ssh-workspaces.md) · [SPEC-REMOTE-SSH-MCP-1](./SPEC-REMOTE-SSH-MCP-1.md)

## 0. Motivation

Remote mutation is only useful if verify runs in the same remote environment as the code.
The local MCP should return structured diagnostics and recovery hints without forcing the
agent into ad-hoc SSH commands.

## R0 RFP traceability gate (meta)

| RFP id | SPEC Rk | Disposition |
|--------|---------|-------------|
| A29-A | — | waiver: SPEC-REMOTE-SSH-MCP-1 |
| A29-B | — | waiver: SPEC-REMOTE-SSH-CORE-1 |
| A29-C | — | waiver: SPEC-REMOTE-SSH-WORKER-1 |
| A29-D | — | waiver: SPEC-REMOTE-SSH-WORKER-1 |
| A29-E | R1 | covered for verify argv/log channel |
| A29-F | — | waiver: SPEC-REMOTE-SSH-MCP-1 |
| A29-G | — | waiver: SPEC-REMOTE-SSH-MCP-1 |
| A29-H | R4 | covered (remote diagnostics shape; R1-R3 cover argv/log/baseline details) |
| A29-I | R5 | covered |
| A29-J | R2 | covered for verify env/log redaction |
| A29-K | R4 | covered diagnostics metadata |
| A29-L | R5 | covered |
| A29-M | R1 | fake transport tests |
| A29-N | — | waiver: SPEC-REMOTE-SSH-MCP-1 docs |

(verify: python3 -m pytest tests/test_remote_verify.py::test_r0_remote_verify_rfp_coverage -q)
| A29-O | — | waiver: SPEC-REMOTE-SSH-MCP-1 |

## R1 Remote verify argv and shell policy

`apatch_verify_run(target_dir='ssh://host/path', verify=[...])` executes argv on the
remote worker without shell interpolation. Shell-string verify remains supported only via
existing apatch semantics and is marked higher risk in the response.

(verify: python3 -m pytest tests/test_remote_verify.py::test_r1_remote_verify_argv -q)

## R2 Bounded log streaming and redaction

Remote verify captures stdout/stderr with byte and line limits. Environment values marked
secret are redacted. Truncation is explicit in the response, not silent.

(verify: python3 -m pytest tests/test_remote_verify.py::test_r2_remote_verify_logs_bounded_and_redacted -q)

## R3 Baseline capture/compare on remote

`baseline='capture'` writes the baseline under remote `.apatch`; `baseline='compare'`
compares remote failures against that baseline. No baseline file is written to the local
Mac for the remote repo.

(verify: python3 -m pytest tests/test_remote_verify.py::test_r3_remote_baseline_capture_compare -q)

## R4 Structured diagnostics survive transport

Compiler/test diagnostics collected on the remote host return as normal
`diagnostics[]`, with remote file paths preserved and optional local display labels.
Knowledge graph/status can reference the remote diagnostics artifact.

(verify: python3 -m pytest tests/test_remote_verify.py::test_r4_remote_diagnostics_shape -q)

## R5 SSH disconnect resume

If SSH disconnects during verify/apply, a follow-up call re-reads remote session state and
returns a valid next action: poll verify status, resume session, rollback remote checkpoint,
or session_end. Local state never forces rollback.

(verify: python3 -m pytest tests/test_remote_verify.py::test_r5_remote_disconnect_resume -q)

## Non-goals

- Real network SSH in CI.
- Replacing remote CI systems.
- Streaming unlimited logs.