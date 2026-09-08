# SPEC-REMOTE-SSH-WORKER-1 — SSH transport and remote worker protocol

> **Status:** Draft v1 · **Owner:** apatch core  
> **apatch artifact:** `spec:SPEC-REMOTE-SSH-WORKER-1`  
> **Anchors:** [RFP-029](../RFP-029-remote-ssh-workspaces.md) · [SPEC-REMOTE-SSH-CORE-1](./SPEC-REMOTE-SSH-CORE-1.md)

## 0. Motivation

Local MCP should stay local, but governed operations must run where the repo lives. The
least fragile design is a remote Python worker invoked over SSH: local apatch sends one
JSON envelope, remote apatch executes one operation, stdout returns one JSON object.

## R0 RFP traceability gate (meta)

| RFP id | SPEC Rk | Disposition |
|--------|---------|-------------|
| A29-A | R1 | covered |
| A29-B | — | waiver: SPEC-REMOTE-SSH-CORE-1 |
| A29-C | R3 | covered (remote doctor/bootstrap handshake) |
| A29-D | R2 | covered |
| A29-E | R4 | covered |
| A29-F | — | waiver: SPEC-REMOTE-SSH-MCP-1 |
| A29-G | — | waiver: SPEC-REMOTE-SSH-MCP-1 |
| A29-H | — | waiver: SPEC-REMOTE-SSH-VERIFY-1 |
| A29-I | R5 | covered at protocol reconnect level; full workflow resume in VERIFY |
| A29-J | R4 | covered |
| A29-K | — | waiver: SPEC-REMOTE-SSH-MCP-1 |
| A29-L | R5 | covered |
| A29-M | R1 | fake transport tests, no network |
| A29-N | — | waiver: SPEC-REMOTE-SSH-MCP-1 docs |

(verify: python3 -m pytest tests/test_remote_worker_protocol.py::test_r0_worker_rfp_coverage -q)
| A29-O | — | waiver: SPEC-REMOTE-SSH-MCP-1 |

## R1 Transport abstraction

`RemoteTransport` has a fake implementation for tests and an SSH implementation for real
hosts. The public call accepts `RemoteTarget`, operation name, and kwargs dict; it returns
parsed JSON or typed `REMOTE_PROTOCOL_ERROR`. Tests do not require network.

(verify: python3 -m pytest tests/test_remote_worker_protocol.py::test_r1_fake_transport_round_trip -q)

## R2 Worker envelope and dispatch

The remote worker entrypoint reads one JSON envelope from stdin, validates
`protocol_version`, resolves `target_dir` on the remote host, dispatches only allowlisted
apatch operations, and writes exactly one JSON object to stdout. Any Rich/progress output
is redirected to stderr/log capture.

(verify: python3 -m pytest tests/test_remote_worker_protocol.py::test_r2_worker_envelope_dispatch -q)

## R3 Remote doctor/bootstrap handshake

`remote_doctor(target)` checks SSH reachability, remote Python, remote git root, remote
apatch import, remote `.apatch` presence, and remote tool fingerprint. Missing apatch or
version skew returns typed remediation; it does not silently run package installation.

(verify: python3 -m pytest tests/test_remote_worker_protocol.py::test_r3_remote_doctor_handshake -q)

## R4 Injection-safe argv construction

SSH invocation uses a fixed remote command (`python -m apatch.remote.worker`) and passes
operation data via stdin JSON. Target paths, verify strings, and needles do not become SSH
command-line fragments. Injection fixtures remain inert data in the worker request.

(verify: python3 -m pytest tests/test_remote_worker_protocol.py::test_r4_ssh_command_does_not_embed_user_data -q)

## R5 Protocol error and reconnect semantics

Non-zero SSH exit, timeout, invalid JSON stdout, protocol version mismatch, and worker
exceptions map to typed errors. A failed transport call does not mutate local session
state; subsequent calls can re-run remote doctor or remote session_state.

(verify: python3 -m pytest tests/test_remote_worker_protocol.py::test_r5_protocol_errors_are_typed -q)

## Non-goals

- Mutating workflow routing (SPEC-REMOTE-SSH-MCP-1).
- Long-running verify jobs (SPEC-REMOTE-SSH-VERIFY-1).
- Auto-installing remote dependencies.