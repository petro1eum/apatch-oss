"""External inclusion proofs — the ledger is tamper-proof off-machine (RFP-005 §0.3).

The local ``.trustchain/`` ledger lives on the same disk as the code it
attests; anyone who can rewrite the code can rewrite (or delete) the ledger.
Tamper-*proofness* therefore requires an **external append-only anchor**: the
TrustChain Platform verifiable log (a Merkle tree). When apatch pushes a step
to the Platform it records the returned ``op_id`` (content-addressable, derived
from the signed envelope). CI then proves — independently, against the public
log — that every recorded operation is included under the published Merkle
root. A wiped or rewritten local ledger cannot erase what the external log
already committed.

Flow:

* ``record_inclusion`` — append ``{op_id, tool, signature}`` to
  ``.apatch/inclusion.jsonl`` when a push succeeds (best-effort, additive).
* ``verify_inclusion`` — for each recorded ``op_id``, fetch the public
  inclusion proof (``GET /api/pub/log/proof/{op_id}``) and verify the Merkle
  audit path from the leaf hash up to the proof's root. Missing or inconsistent
  entries fail the gate (under enforcement, via ``ci-gate``).

Verification needs only the leaf hash + sibling path (no server-side leaf
serialization to reproduce), so it is fully checkable client-side.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

INCLUSION_REL = os.path.join(".apatch", "inclusion.jsonl")


def inclusion_log_path(root: str = ".") -> str:
    return os.path.join(os.path.abspath(root), INCLUSION_REL)


# Cap the committed inclusion manifest: the public append-only log is the permanent
# record, so the local manifest only needs a recent window — this bounds git churn and
# the per-CI-run verification cost (one HTTP proof fetch per record). Older ops stay
# verifiable in the public log forever.
INCLUSION_CAP = 250


def record_inclusion(
    root: str,
    *,
    op_id: str,
    tool: Optional[str] = None,
    signature: Optional[str] = None,
) -> None:
    """Append an external-anchor record (idempotent by ``op_id``), keeping only the
    last ``INCLUSION_CAP`` records.

    Best-effort: never raises into the signing path.
    """
    if not op_id or not isinstance(op_id, str):
        return
    try:
        from apatch.runtime.atomic_io import exclusive_file_lock

        path = inclusion_log_path(root)
        with exclusive_file_lock(path):
            existing = load_inclusion_records(root)
            if op_id in {r.get("op_id") for r in existing}:
                return
            os.makedirs(os.path.dirname(path), exist_ok=True)
            rec = {
                "op_id": op_id,
                "tool": tool,
                "signature": signature,
                "recorded_at": datetime.now(timezone.utc).isoformat(),
            }
            records = (existing + [rec])[-INCLUSION_CAP:]
            tmp = "{}.{}.tmp".format(path, os.getpid())
            with open(tmp, "w", encoding="utf-8") as fh:
                for r in records:
                    fh.write(json.dumps(r, ensure_ascii=False) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
    except OSError:
        pass


def load_inclusion_records(root: str = ".") -> List[Dict[str, Any]]:
    path = inclusion_log_path(root)
    if not os.path.isfile(path):
        return []
    out: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if isinstance(rec, dict) and rec.get("op_id"):
                        out.append(rec)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return out


def _hash_pair(left: str, right: str) -> str:
    """Internal-node Merkle hash with 0x01 domain separation. Vendored from
    ``trustchain.v2.merkle.hash_pair`` so verifying a PUBLIC inclusion proof never
    requires the private ``trustchain`` package (CI / any third-party auditor)."""
    return hashlib.sha256(("\x01" + left + right).encode("utf-8")).hexdigest()


def verify_audit_path(
    chunk_hash: str, siblings: List, expected_root: str
) -> bool:
    """Walk a Merkle audit path from the leaf hash to the root.

    Mirrors ``trustchain.v2.merkle.verify_proof`` but starts from the leaf
    *hash* (not the preimage), so a verifier needs only the proof — never the
    server's leaf serialization. Self-contained: no ``trustchain`` import.
    """
    hash_pair = _hash_pair

    current = chunk_hash
    for sib in siblings:
        # siblings entries are (hash, position) pairs.
        try:
            sib_hash, position = sib[0], sib[1]
        except (TypeError, IndexError):
            return False
        if position == "left":
            current = hash_pair(sib_hash, current)
        else:
            current = hash_pair(current, sib_hash)
    return current == expected_root


def _fetch_merkle_root(base_url: str, http_client, timeout: float):
    """Return (head_dict | None, error | None). head has merkle_root + length."""
    base = base_url.rstrip("/")
    url = f"{base}/api/pub/log/merkle-root"
    try:
        if http_client is not None:
            client = http_client
            close = False
        else:
            import httpx

            client = httpx.Client(timeout=timeout)
            close = True
        try:
            resp = client.get(url)
            if resp.status_code != 200:
                return None, f"http {resp.status_code}"
            data = resp.json()
            root = data.get("merkle_root")
            length = data.get("length")
            if root is None or length is None:
                return None, "malformed merkle-root response"
            return {"merkle_root": root, "length": int(length)}, None
        finally:
            if close:
                client.close()
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


def _check_log_consistency(
    proofs_meta: List[tuple],
    current: Dict[str, Any],
) -> tuple:
    """Detect log fork / stale head from proof snapshots vs current Merkle head.

    ``proofs_meta`` is a list of ``(op_id, chain_length, root_at_proof_time)``
    tuples collected from verified inclusion proofs.
    """
    errors: List[str] = []
    current_len = int(current.get("length", 0))
    current_root = current.get("merkle_root")

    roots_by_len: Dict[int, str] = {}
    for op_id, chain_len, root in proofs_meta:
        if chain_len > current_len:
            errors.append(
                f"{op_id[:12]}…: proof chain_length {chain_len} > current {current_len}"
            )
            continue
        prev = roots_by_len.get(chain_len)
        if prev is not None and prev != root:
            errors.append(
                f"log fork at length {chain_len}: conflicting Merkle roots "
                f"({prev[:12]}… vs {root[:12]}…)"
            )
        roots_by_len[chain_len] = root
        if chain_len == current_len and root != current_root:
            errors.append(
                f"{op_id[:12]}…: proof root != current merkle_root at head "
                f"(length {current_len})"
            )

    return len(errors) == 0, errors


def _coverage_check(root: str, anchored: int) -> Dict[str, Any]:
    """Compare anchored external ops vs locally signed ledger entries.

    Only enforced once anchoring has started (``anchored > 0``): if the
    operator pushes to Platform they must not leave local signed steps
    unanchored.
    """
    if anchored <= 0:
        return {"ok": True, "skipped": "no anchored ops yet"}
    try:
        from apatch.platform_client import platform_config_from_env
        from apatch.trustchain_helper import TrustChainHelper

        if not platform_config_from_env():
            return {"ok": True, "skipped": "no platform configured"}
        helper = TrustChainHelper(root)
        local_signed = helper.count_signed_blocks()
        if local_signed == 0:
            return {"ok": True, "local_signed": 0, "anchored": anchored}
        uncovered = max(0, local_signed - anchored)
        return {
            "ok": uncovered == 0,
            "local_signed": local_signed,
            "anchored": anchored,
            "uncovered": uncovered,
        }
    except Exception:
        return {"ok": True, "skipped": "coverage check unavailable"}


def _fetch_proof(base_url: str, op_id: str, http_client, timeout: float):
    """Return (proof_dict | None, error | None). error 'not_found' on 404."""
    import urllib.parse

    base = base_url.rstrip("/")
    url = f"{base}/api/pub/log/proof/{urllib.parse.quote(op_id, safe='')}"
    try:
        if http_client is not None:
            client = http_client
            close = False
        else:
            import httpx

            client = httpx.Client(timeout=timeout)
            close = True
        try:
            resp = client.get(url)
            if resp.status_code == 404:
                return None, "not_found"
            if resp.status_code != 200:
                return None, f"http {resp.status_code}"
            return resp.json(), None
        finally:
            if close:
                client.close()
    except Exception as exc:  # noqa: BLE001 — network/parse failures are reported, not raised
        return None, str(exc)


def verify_inclusion(
    root: str = ".",
    *,
    base_url: Optional[str] = None,
    http_client: Any = None,
    timeout: float = 15.0,
) -> Dict[str, Any]:
    """Prove every recorded op_id is included in the external append-only log.

    Opt-in: with no recorded inclusions or no Platform configured, returns
    ``ok=True`` with ``skipped`` set (the gate stays green for repos that don't
    anchor externally). Otherwise every recorded op_id must resolve to a valid
    inclusion proof; ``missing`` / ``inconsistent`` fail it (tamper). ``errors``
    (the log was unreachable) set ``degraded=True`` but do NOT fail — absence of a
    fetched proof is not evidence of forgery, and a platform blip must not red CI.
    """
    records = load_inclusion_records(root)
    base_url = base_url or os.environ.get("APATCH_PLATFORM_URL", "").strip() or None

    if not records:
        return {"ok": True, "checked": 0, "skipped": "no inclusion records"}
    if not base_url:
        return {
            "ok": True,
            "checked": 0,
            "skipped": "no platform configured (APATCH_PLATFORM_URL unset)",
            "recorded": len(records),
        }

    verified: List[str] = []
    missing: List[str] = []
    inconsistent: List[str] = []
    errors: List[str] = []
    proofs_meta: List[tuple] = []

    for rec in records:
        op_id = rec["op_id"]
        proof, err = _fetch_proof(base_url, op_id, http_client, timeout)
        if err == "not_found":
            missing.append(op_id)
            continue
        if err is not None:
            errors.append(f"{op_id[:12]}…: {err}")
            continue
        try:
            mp = proof.get("proof") or {}
            proof_root = proof.get("root") or mp.get("root")
            chain_len = proof.get("chain_length")
            if proof.get("op_id") and proof.get("op_id") != op_id:
                inconsistent.append(op_id)
                continue
            if not proof_root or not mp.get("chunk_hash"):
                inconsistent.append(op_id)
                continue
            if verify_audit_path(mp["chunk_hash"], mp.get("siblings") or [], proof_root):
                verified.append(op_id)
                if chain_len is not None:
                    proofs_meta.append((op_id, int(chain_len), proof_root))
            else:
                inconsistent.append(op_id)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{op_id[:12]}…: malformed proof ({exc})")

    consistency_ok = True
    consistency_errors: List[str] = []
    current_head: Optional[Dict[str, Any]] = None
    if verified and not missing and not inconsistent and not errors:
        current_head, head_err = _fetch_merkle_root(base_url, http_client, timeout)
        if head_err is not None:
            errors.append(f"merkle-root: {head_err}")
        elif current_head is not None:
            consistency_ok, consistency_errors = _check_log_consistency(
                proofs_meta, current_head
            )
            if not consistency_ok:
                errors.extend(consistency_errors)

    coverage = _coverage_check(root, len(records))
    coverage_ok = coverage.get("ok", True)

    # Block only on TAMPER — a recorded op missing from the (reachable) log or a
    # proof that does not verify. Network/fetch failures (``errors``) mean the log
    # was unreachable, not that governance was forged: report ``degraded`` and do
    # NOT fail the gate, so a platform blip can't red an otherwise-clean CI run.
    ok = (
        not missing
        and not inconsistent
        and consistency_ok
        and coverage_ok
    )
    return {
        "ok": ok,
        "degraded": bool(errors),
        "checked": len(records),
        "verified": len(verified),
        "missing": missing,
        "inconsistent": inconsistent,
        "errors": errors,
        "consistency_ok": consistency_ok,
        "consistency_errors": consistency_errors,
        "current_head": current_head,
        "coverage": coverage,
        "base_url": base_url,
    }


def inclusion_status(root: str = ".") -> Dict[str, Any]:
    """Compact external-anchor posture for ``doctor``."""
    records = load_inclusion_records(root)
    platform = bool(os.environ.get("APATCH_PLATFORM_URL", "").strip())
    if not records:
        return {
            "anchored": False,
            "recorded": 0,
            "platform_configured": platform,
        }
    try:
        from apatch.trustchain_helper import TrustChainHelper

        local_signed = TrustChainHelper(root).count_signed_blocks()
    except Exception:
        local_signed = None
    return {
        "anchored": True,
        "recorded": len(records),
        "platform_configured": platform,
        "local_signed": local_signed,
        "uncovered": (
            max(0, local_signed - len(records))
            if local_signed is not None
            else None
        ),
    }
