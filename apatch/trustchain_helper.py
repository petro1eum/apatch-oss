import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from apatch.path_index import prune_walk_dirs

if TYPE_CHECKING:
    from apatch.backup import BackupManager


def _artifact_file_partition_valid(payload: Dict[str, Any]) -> bool:
    """Fail closed when a partitioned mutation does not cover exact artifacts/files."""
    partition = payload.get("artifact_files")
    if not partition:
        return True
    if not isinstance(partition, dict):
        return False
    artifacts = payload.get("artifacts") or []
    artifact_keys = {
        "{}:{}".format(art.get("kind"), art.get("id"))
        for art in artifacts
        if isinstance(art, dict) and art.get("kind") and art.get("id")
    }
    if set(partition) != artifact_keys:
        return False

    from apatch.apatch_paths import normalize_rel

    assigned: Dict[str, str] = {}
    for key, raw_paths in partition.items():
        if not isinstance(raw_paths, list) or not raw_paths:
            return False
        for raw_path in raw_paths:
            rel = normalize_rel(str(raw_path))
            if not rel or rel in assigned:
                return False
            assigned[rel] = str(key)

    files = payload.get("files")
    if isinstance(files, dict):
        for raw_path in files:
            if normalize_rel(str(raw_path)) not in assigned:
                return False
    return True


class TrustChainHelper:
    """
    TrustChain integration for apatch: auto-init .trustchain/, checkpoints, commits.

    For strip/apply, an apatch **checkpoint** is one session id that always includes:
    - physical file backup under `.apatch/backups/<checkpoint_name>/`
    - TrustChain HEAD ref at `refs/checkpoints/<checkpoint_name>.ref` (when enabled)
    - signed ledger entry listing backed-up paths and SHA-256 (when enabled)

    `rollback_checkpoint` / `rollback_session` restore **both** disk and HEAD.
    """

    _MARKER_DIRS = (".git",)
    _MARKER_FILES = ("pyproject.toml", "CMakeLists.txt", "go.mod", "package.json")

    def __init__(self, target_dir: str, auto_init: bool = True):
        self.auto_init = auto_init
        self.target_dir = os.path.abspath(target_dir)
        self.workspace_root = self.resolve_workspace_root(self.target_dir)
        self.trustchain_dir = self._detect_trustchain_dir()
        self._physical_backups: dict[str, "BackupManager"] = {}
        self._signature_index: Optional[Dict[str, str]] = None
        self._last_commit_evidence: Optional[Dict[str, Any]] = None
        if not self.trustchain_dir and auto_init:
            self._auto_init_trustchain()
        if self.trustchain_dir:
            self._ensure_reversibles()
            self._ensure_policy_hooks()
        from apatch.enforcement import ensure_enforcement_hooks

        ensure_enforcement_hooks(self.workspace_root)

    @classmethod
    def resolve_workspace_root(cls, start_path: str) -> str:
        """Walk up from start_path to find repo root (git / build markers)."""
        cur = os.path.abspath(start_path)
        if os.path.isfile(cur):
            cur = os.path.dirname(cur)
        best = cur
        while True:
            if os.path.exists(os.path.join(cur, ".git")):
                return cur
            if any(os.path.exists(os.path.join(cur, name)) for name in cls._MARKER_FILES):
                best = cur
            parent = os.path.dirname(cur)
            if parent == cur:
                return best
            cur = parent

    def _detect_trustchain_dir(self) -> Optional[str]:
        """Return only the ledger owned by this exact workspace root.

        ``workspace_root`` already resolves nested paths to their repository. Walking
        above it lets linked worktrees inherit an unrelated user-level ledger and
        breaks the one-workspace/one-receipt invariant.
        """
        candidate = os.path.join(self.workspace_root, ".trustchain")
        if os.path.isdir(candidate):
            return os.path.abspath(candidate)
        return None

    def _bootstrap_trustchain_dir(self, root: str) -> bool:
        """Create .trustchain/ layout (same as ``tc init``) without requiring the CLI."""
        trustchain_dir = Path(root) / ".trustchain"
        try:
            (trustchain_dir / "objects").mkdir(parents=True, exist_ok=True)
            (trustchain_dir / "chain").mkdir(parents=True, exist_ok=True)
            (trustchain_dir / "refs" / "sessions").mkdir(parents=True, exist_ok=True)
            (trustchain_dir / "refs" / "checkpoints").mkdir(parents=True, exist_ok=True)
            (trustchain_dir / "refs" / "tags").mkdir(parents=True, exist_ok=True)
            head_file = trustchain_dir / "HEAD"
            if not head_file.exists():
                head_file.write_text("", encoding="utf-8")
            config_file = trustchain_dir / "config.json"
            if not config_file.exists():
                config_file.write_text(
                    json.dumps(
                        {
                            "version": 1,
                            "algorithm": "Ed25519",
                            "created_by": "apatch (auto-init)",
                        },
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            metadata_file = trustchain_dir / "metadata.json"
            if not metadata_file.exists():
                metadata_file.write_text(
                    json.dumps({"initialized_by": "apatch", "auto_init": True}, indent=2),
                    encoding="utf-8",
                )
            return True
        except OSError:
            return False

    def _auto_init_trustchain(self) -> None:
        root = self.workspace_root
        if self._detect_trustchain_dir():
            self.trustchain_dir = self._detect_trustchain_dir()
            return

        if shutil.which("tc"):
            try:
                res = subprocess.run(
                    ["tc", "init", "-o", root],
                    capture_output=True,
                    text=True,
                    cwd=root,
                )
                if res.returncode == 0:
                    self.trustchain_dir = self._detect_trustchain_dir()
                    if self.trustchain_dir:
                        return
            except OSError:
                pass

        if self._bootstrap_trustchain_dir(root):
            self.trustchain_dir = os.path.join(root, ".trustchain")

    def _ensure_reversibles(self) -> None:
        if not self.trustchain_dir:
            return
        mapping = {"apatch": "apatch_rollback"}
        try:
            from trustchain.v3.compensations import register_reversible

            register_reversible("apatch", "apatch_rollback")
        except ImportError:
            pass
        rev_path = os.path.join(self.trustchain_dir, "reversibles.json")
        if os.path.exists(rev_path):
            try:
                with open(rev_path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                if isinstance(existing, dict):
                    mapping = {**existing, **mapping}
            except (OSError, json.JSONDecodeError):
                pass
        try:
            with open(rev_path, "w", encoding="utf-8") as f:
                json.dump(mapping, f, indent=2)
        except OSError:
            pass

    def _ensure_policy_hooks(self) -> None:
        deny_raw = os.environ.get("APATCH_POLICY_DENY", "").strip()
        if not deny_raw:
            return
        denied = {t.strip() for t in deny_raw.split(",") if t.strip()}
        if not denied:
            return
        try:
            from trustchain.v2.policy_hooks import get_policy_registry

            def _apatch_deny_list_hook(response, context):
                return response.tool_id not in denied

            reg = get_policy_registry()
            if not any(getattr(h, "__name__", "") == "_apatch_deny_list_hook" for h in reg._hooks):
                reg.register(_apatch_deny_list_hook)
        except ImportError:
            pass

    def _policy_allows(self, tool_id: str, payload: dict) -> bool:
        try:
            from trustchain.v2.policy_hooks import get_policy_registry
            from trustchain.v2.signer import SignedResponse

            response = SignedResponse(
                tool_id=tool_id,
                data=payload,
                signature="pending",
            )
            passed, err = get_policy_registry().evaluate(
                response, {"source": "apatch", "workspace": self.workspace_root}
            )
            if not passed:
                tid = getattr(response, "tool_id", None) or ""
                print(
                    f"[apatch] policy blocked commit (tool_id={tid!r}): {err}",
                    file=sys.stderr,
                )
            return passed
        except ImportError:
            return True

    def has_trustchain(self) -> bool:
        return self.trustchain_dir is not None

    def ensure_head(self) -> bool:
        """Ensure HEAD is non-empty so ``tc checkpoint`` can run."""
        if not self.has_trustchain():
            return False
        head_path = os.path.join(self.trustchain_dir, "HEAD")
        try:
            with open(head_path, "r", encoding="utf-8") as f:
                if f.read().strip():
                    return True
        except OSError:
            return False
        return self.commit_action(
            "apatch",
            {"action": "bootstrap", "workspace": self.workspace_root},
        )

    def create_checkpoint(self, name: str) -> Optional[str]:
        if not self.has_trustchain():
            return None
        if not self.ensure_head():
            return None
        from apatch.runtime.atomic_io import exclusive_file_lock

        try:
            with exclusive_file_lock(self._ledger_lock_path()):
                cmd = ["tc", "checkpoint", name, "--dir", self.trustchain_dir]
                if shutil.which("tc"):
                    res = subprocess.run(cmd, capture_output=True, text=True, cwd=self.workspace_root)
                    if res.returncode == 0:
                        return name
                return self._checkpoint_via_python(name)
        except OSError:
            with exclusive_file_lock(self._ledger_lock_path()):
                return self._checkpoint_via_python(name)

    def _ledger_lock_path(self) -> str:
        return os.path.join(self.workspace_root, ".apatch", "trustchain_ledger")

    def _checkpoint_via_python(self, name: str) -> Optional[str]:
        try:
            from trustchain import TrustChain

            tc = TrustChain(self._tc_config())
            store = getattr(tc, "_store", None) or getattr(tc, "store", None)
            if store and hasattr(store, "checkpoint"):
                store.checkpoint(name)
                return name
        except Exception:
            pass
        head_path = os.path.join(self.trustchain_dir, "HEAD")
        try:
            with open(head_path, "r", encoding="utf-8") as f:
                head = f.read().strip()
            if not head:
                return None
            safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in name)
            ref_dir = os.path.join(self.trustchain_dir, "refs", "checkpoints")
            os.makedirs(ref_dir, exist_ok=True)
            with open(os.path.join(ref_dir, f"{safe}.ref"), "w", encoding="utf-8") as f:
                f.write(head + "\n")
            return name
        except OSError:
            return None

    @staticmethod
    def _file_sha256(path: str) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

    def _restore_physical_for_checkpoint(self, checkpoint_name: str) -> List[str]:
        """Restore files from the BackupManager session bound to this checkpoint."""
        mgr = self._physical_backups.get(checkpoint_name)
        if mgr is None:
            from apatch.backup import BackupManager

            sess_path = os.path.join(
                self.workspace_root, ".apatch", "backups", checkpoint_name
            )
            if os.path.isdir(sess_path):
                mgr = BackupManager(self.workspace_root, session_id=checkpoint_name)
        if mgr is None:
            return []

        restored: List[str] = []
        for entry in mgr._load_existing_backups():
            orig = entry.get("original_file")
            if orig and mgr.restore_file(orig):
                restored.append(orig)
        if not restored:
            from apatch.backup import BackupManager as _BackupManager

            try:
                restored = _BackupManager.rollback_session(mgr.target_dir, mgr.session_id)
            except ValueError:
                pass
        self._physical_backups.pop(checkpoint_name, None)
        return restored

    def commit_checkpoint_files(
        self, checkpoint_name: str, source_files: List[str]
    ) -> bool:
        """Record checkpoint file snapshots in the TrustChain ledger."""
        files_meta: Dict[str, Dict[str, str]] = {}
        for path in source_files:
            abs_path = os.path.abspath(path)
            if os.path.isfile(abs_path):
                files_meta[abs_path] = {
                    "sha256": self._file_sha256(abs_path),
                    "backup_session": checkpoint_name,
                }
        if not files_meta:
            return False
        return self.commit_action(
            "apatch",
            {
                "action": "checkpoint",
                "checkpoint": checkpoint_name,
                "files": files_meta,
            },
        )

    def rollback_checkpoint(
        self,
        name: str,
        *,
        governed_session_id: Optional[str] = None,
    ) -> bool:
        """
        Roll back checkpoint: restore backed-up files on disk, then TrustChain HEAD.
        """
        from apatch.sandbox import acquire_lease, load_active_lease, release_lease

        backup_paths: List[str] = []
        mgr = self._physical_backups.get(name)
        if mgr is None:
            from apatch.backup import BackupManager

            session_dir = os.path.join(self.workspace_root, ".apatch", "backups", name)
            if os.path.isdir(session_dir):
                mgr = BackupManager(self.workspace_root, session_id=name)
        if mgr is not None:
            for entry in mgr._load_existing_backups():
                original = entry.get("original_file")
                if original:
                    backup_paths.append(
                        os.path.relpath(os.path.abspath(original), self.workspace_root)
                    )

        lease_owner = governed_session_id or name
        existing = load_active_lease(
            self.workspace_root,
            governed_session_id=lease_owner,
        )
        acquired = None
        if backup_paths:
            acquired = acquire_lease(
                self.workspace_root,
                backup_paths,
                tool="apatch_rollback",
                governed_session_id=lease_owner,
                session_checkpoint=name,
            )
        files_ok = bool(self._restore_physical_for_checkpoint(name))

        def release_rollback_lease() -> None:
            if acquired and existing is None:
                release_lease(
                    self.workspace_root,
                    lease_id=acquired.get("lease_id"),
                    governed_session_id=lease_owner,
                )

        if not self.has_trustchain():
            release_rollback_lease()
            return files_ok

        from apatch.path_leases import registry_enabled

        if registry_enabled(self.workspace_root):
            restored_meta: Dict[str, Dict[str, str]] = {}
            for rel in backup_paths:
                absolute = os.path.join(self.workspace_root, rel)
                if os.path.isfile(absolute):
                    restored_meta[rel] = {"sha256": self._file_sha256(absolute)}
            recorded = self.commit_action(
                "apatch",
                {
                    "action": "rollback_compensation",
                    "checkpoint": name,
                    "governed_session_id": lease_owner,
                    "files": restored_meta,
                },
            )
            release_rollback_lease()
            return files_ok or recorded

        try:
            ref_path = os.path.join(self.trustchain_dir, "refs", "checkpoints", f"{name}.ref")
            if not os.path.exists(ref_path):
                safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in name)
                ref_path = os.path.join(self.trustchain_dir, "refs", "checkpoints", f"{safe}.ref")
            if not os.path.exists(ref_path):
                release_rollback_lease()
                return files_ok

            with open(ref_path, "r", encoding="utf-8") as f:
                target_sig = f.read().strip()

            op_id = self._find_op_id_by_signature(target_sig)
            if op_id and shutil.which("tc"):
                cmd = ["tc", "reset", op_id, "--soft", "--dir", self.trustchain_dir]
                subprocess.run(cmd, capture_output=True, cwd=self.workspace_root)
                self._maybe_push_revert_to_platform(op_id, f"checkpoint rollback {name}")
                release_rollback_lease()
                return files_ok or True
            with open(os.path.join(self.trustchain_dir, "HEAD"), "w", encoding="utf-8") as f:
                f.write(target_sig + "\n")
            if op_id:
                self._maybe_push_revert_to_platform(op_id, f"checkpoint rollback {name}")
            release_rollback_lease()
            return files_ok or True
        except OSError:
            release_rollback_lease()
            return files_ok

    def _build_signature_index(self) -> Dict[str, str]:
        index: Dict[str, str] = {}
        objects_dir = os.path.join(self.trustchain_dir or "", "objects")
        if not self.trustchain_dir or not os.path.isdir(objects_dir):
            return index
        for root, dirs, files in os.walk(objects_dir):
            prune_walk_dirs(dirs)
            for filename in files:
                if not filename.endswith(".json"):
                    continue
                path = os.path.join(root, filename)
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    sig = data.get("signature")
                    op_id = data.get("id") or data.get("signature_id")
                    if sig is None and "value" in data and isinstance(data["value"], dict):
                        val = data["value"]
                        sig = val.get("signature")
                        op_id = val.get("id") or val.get("signature_id")
                    if sig and op_id:
                        index[sig] = op_id
                except (OSError, json.JSONDecodeError):
                    pass
        return index

    @staticmethod
    def _parse_ledger_object(data: dict) -> Dict[str, Any]:
        """Normalize a TrustChain object JSON blob into tool_id + inner payload."""
        val = data.get("value") if isinstance(data.get("value"), dict) else data
        tool_id = None
        inner: dict = {}
        if isinstance(val, dict):
            tool_id = val.get("tool_id") or val.get("tool")
            raw = val.get("data")
            if isinstance(raw, dict):
                inner = raw
            elif isinstance(val.get("payload"), dict):
                inner = val["payload"]
            elif "intent" in val or "adr" in val or "action" in val:
                inner = val
        op_id = data.get("id") or data.get("signature_id")
        signature = data.get("signature")
        if isinstance(val, dict):
            op_id = op_id or val.get("id") or val.get("signature_id")
            signature = signature or val.get("signature")
        ts = data.get("timestamp") or data.get("created_at")
        if isinstance(val, dict) and not ts:
            ts = val.get("timestamp") or val.get("created_at")
        key_id = data.get("key_id")
        algorithm = data.get("algorithm")
        if isinstance(val, dict):
            key_id = key_id or val.get("key_id")
            algorithm = algorithm or val.get("algorithm")
        return {
            "id": op_id,
            "signature": signature,
            "tool_id": tool_id,
            "timestamp": ts,
            "payload": inner if isinstance(inner, dict) else {},
            "key_id": key_id,
            "algorithm": algorithm,
        }

    def _iter_ledger_json_paths(self) -> List[str]:
        """All ledger JSON paths: ``objects/*.json`` and ``chain/block_*.json``."""
        if not self.trustchain_dir:
            return []
        out: List[str] = []
        objects_dir = os.path.join(self.trustchain_dir, "objects")
        if os.path.isdir(objects_dir):
            for root, dirs, files in os.walk(objects_dir):
                prune_walk_dirs(dirs)
                for filename in sorted(files):
                    if filename.endswith(".json"):
                        out.append(os.path.join(root, filename))
        chain_dir = os.path.join(self.trustchain_dir, "chain")
        if os.path.isdir(chain_dir):
            for filename in sorted(os.listdir(chain_dir)):
                if filename.startswith("block_") and filename.endswith(".json"):
                    out.append(os.path.join(chain_dir, filename))
        return out

    @staticmethod
    def _object_has_ed25519_signature(data: dict) -> bool:
        sig = data.get("signature")
        if isinstance(sig, str) and len(sig) > 16:
            return True
        val = data.get("value")
        if isinstance(val, dict):
            inner = val.get("signature")
            if isinstance(inner, str) and len(inner) > 16:
                return True
        return False

    def count_signed_blocks(self) -> int:
        """Count JSON ledger files that carry an Ed25519 signature field."""
        n = 0
        for path in self._iter_ledger_json_paths():
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                if self._object_has_ed25519_signature(data):
                    n += 1
            except (OSError, json.JSONDecodeError):
                pass
        return n

    def iter_ledger_entries(self) -> List[Dict[str, Any]]:
        """Walk ledger files (objects + chain/block_*) — normalized rows."""
        rows: List[Dict[str, Any]] = []
        if not self.trustchain_dir:
            return rows
        for path in self._iter_ledger_json_paths():
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                row = self._parse_ledger_object(data)
                row["object_path"] = os.path.relpath(path, self.trustchain_dir)
                rows.append(row)
            except (OSError, json.JSONDecodeError):
                pass
        return rows

    def list_intent_history(
        self,
        query: Optional[str] = None,
        *,
        artifact: Optional[str] = None,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """Return pipeline intent/artifact checkpoints recorded in the TrustChain ledger."""
        from apatch.artifact import entry_matches_artifact_filter, entry_search_blob, payload_artifacts

        if not self.has_trustchain():
            return {"ok": False, "error": "no trustchain", "entries": []}
        q = (query or "").strip().lower()
        art_filter = (artifact or "").strip() or None
        hits: List[Dict[str, Any]] = []
        for row in self.iter_ledger_entries():
            payload = row.get("payload") or {}
            intent = payload.get("intent")
            adr = payload.get("adr")
            artifacts = payload_artifacts(payload)
            if not intent and not adr and not artifacts:
                continue
            from apatch.ledger_actor import ledger_signed_by

            entry = {
                "id": row.get("id"),
                "signature": row.get("signature"),
                "tool_id": row.get("tool_id"),
                "timestamp": row.get("timestamp"),
                "intent": intent,
                "adr": adr,
                "artifacts": artifacts,
                "action": payload.get("action"),
                "manifest": payload.get("manifest"),
                "signed_by": ledger_signed_by(row),
            }
            if art_filter and not entry_matches_artifact_filter(payload, art_filter):
                continue
            if q and not art_filter:
                if q not in entry_search_blob(payload):
                    continue
            hits.append(entry)
        if limit > 0:
            hits = hits[-limit:]
        return {
            "ok": True,
            "query": query,
            "artifact": artifact,
            "count": len(hits),
            "entries": hits,
        }

    def artifact_coverage(self, *, artifact: Optional[str] = None) -> Dict[str, Any]:
        """Traceability matrix: artifact → mutations → attestation (RFP-006 §6.2).

        MCP: ``apatch_trustchain_coverage(artifact=...)``. ``coverage.complete`` when both
        mutation and attestation entries are linked to the artifact.
        """
        from apatch.traceability import artifact_coverage_report

        if not self.has_trustchain():
            return {"ok": False, "error": "no trustchain"}
        return artifact_coverage_report(self.iter_ledger_entries(), artifact=artifact)

    def resolve_op_id(self, op_id: str) -> Dict[str, Any]:
        """Reverse map a ledger op_id to linked artifact(s). MCP: ``apatch_trustchain_coverage(op_id=...)``."""
        from apatch.traceability import resolve_op_id_artifacts

        if not self.has_trustchain():
            return {"ok": False, "error": "no trustchain", "op_id": op_id}
        return resolve_op_id_artifacts(self.iter_ledger_entries(), op_id)

    def _find_op_id_by_signature(self, signature: str) -> Optional[str]:
        if not self.trustchain_dir:
            return None
        if self._signature_index is None:
            self._signature_index = self._build_signature_index()
        return self._signature_index.get(signature)

    def _tc_config(self):
        """Build a TrustChainConfig, wiring the enrolled identity when present.

        When ``APATCH_AGENT_ID`` + ``APATCH_AGENT_KEY`` are set, the ledger is
        signed with the enrolled Ed25519 key whose leaf certificate chains to
        the TrustChain root CA (RFP-005 §5.2). Otherwise an ephemeral dev key
        is used (self-signed, no external anchor).

        Compatible with trustchain 2.4 (``key_file`` only, no key binding) and
        3.1+ (``key_provider`` for PEM and command/HSM backends).
        """
        import inspect

        from trustchain import TrustChainConfig

        kwargs = dict(
            enable_chain=True, chain_storage="file", chain_dir=self.trustchain_dir
        )
        try:
            from apatch.trust_identity import load_local_identity

            ident = load_local_identity(self.workspace_root)
            if ident is not None:
                params = inspect.signature(TrustChainConfig.__init__).parameters
                if "key_provider" in params:
                    kwargs["key_provider"] = ident.key_provider
                elif ident.key_path and "key_file" in params:
                    kwargs["key_file"] = ident.key_path
        except Exception:
            pass
        return TrustChainConfig(**kwargs)

    def _enrich_payload_from_governed_session(self, payload: dict) -> dict:
        """Stamp active governed-session intent/artifacts onto ledger commits (RFP-006)."""
        try:
            raw = self._load_governed_session_state()
        except Exception:
            return payload
        if not raw.get("session_id") or raw.get("ended_at"):
            return payload
        enriched = dict(payload)
        enriched.setdefault("governed_session_id", raw["session_id"])
        if raw.get("intent"):
            enriched.setdefault("intent", raw["intent"])
        arts = raw.get("artifacts") or []
        if arts and not enriched.get("artifacts"):
            enriched["artifacts"] = arts
        artifact_files = raw.get("artifact_files") or {}
        if artifact_files and not enriched.get("artifact_files"):
            enriched["artifact_files"] = artifact_files
        try:
            from apatch.ledger_actor import enrich_payload_with_actor
            from apatch.trust_identity import load_local_identity

            ident = load_local_identity(self.workspace_root)
            if ident is not None:
                enriched = enrich_payload_with_actor(enriched, agent_id=ident.agent_id)
        except Exception:
            pass
        return enriched

    def _load_governed_session_state(self) -> dict:
        """Load the active session from the operation target before repo root.

        Remote work often targets a governed sub-workspace while TrustChain is
        stored at a parent repo root. The mutation payload must inherit the
        session that opened in the operation target, not a stale or absent
        parent .apatch/session_state.json.
        """
        from apatch.session_state import load_session_state

        candidates = [self.target_dir]
        if os.path.abspath(self.workspace_root) != os.path.abspath(self.target_dir):
            candidates.append(self.workspace_root)
        first: Optional[dict] = None
        for candidate in candidates:
            raw = load_session_state(candidate)
            if first is None:
                first = raw
            if raw.get("session_id") and not raw.get("ended_at"):
                return raw
        return first or {}

    @property
    def last_commit_evidence(self) -> Dict[str, Any]:
        """O(1) receipt for the most recent commit made by this helper."""
        return dict(self._last_commit_evidence or {})

    def ledger_head(self) -> Optional[str]:
        """Read persisted HEAD without walking ledger objects."""
        if not self.trustchain_dir:
            return None
        try:
            with open(os.path.join(self.trustchain_dir, "HEAD"), encoding="utf-8") as f:
                return f.read().strip() or None
        except OSError:
            return None

    def _validate_python_commit(
        self,
        tc: Any,
        signed: Any,
        *,
        tool_id: str,
        payload: dict,
        before_length: Optional[int],
    ) -> Dict[str, Any]:
        """Validate only the object just appended by TrustChain."""
        chain = getattr(tc, "chain", None)
        signature = getattr(signed, "signature", None)
        after_length = getattr(chain, "length", None)
        in_memory_head = chain.head() if chain is not None and hasattr(chain, "head") else None
        merkle_root = getattr(chain, "merkle_root", None) if chain is not None else None
        disk_head = self.ledger_head()
        length_advanced = (
            isinstance(after_length, int)
            and (before_length is None or after_length == before_length + 1)
        )
        signature_valid = bool(isinstance(signature, str) and len(signature) > 16)
        signer = getattr(tc, "_signer", None)
        if signer is not None and hasattr(signer, "verify"):
            try:
                signature_valid = bool(signer.verify(signed))
            except Exception:
                signature_valid = False

        record: Optional[dict] = None
        object_path: Optional[str] = None
        if chain is not None and length_advanced:
            if merkle_root:
                try:
                    tail = chain.log_reverse(limit=1)
                    record = tail[0] if tail else None
                    if isinstance(record, dict) and record.get("id"):
                        object_path = f"chain.log#{record['id']}"
                except Exception:
                    record = None
            else:
                op_id = f"op_{after_length:04d}"
                try:
                    record = chain.show(op_id)
                except Exception:
                    record = None
                object_path = os.path.join("objects", f"{op_id}.json")

        record_valid = bool(
            isinstance(record, dict)
            and record.get("tool") == tool_id
            and record.get("data") == payload
            and record.get("signature") == signature
        )
        expected_disk_head = merkle_root or signature
        head_valid = bool(
            signature and in_memory_head == signature and disk_head == expected_disk_head
        )
        persisted = bool(length_advanced and signature_valid and record_valid and head_valid)
        return {
            "persisted": persisted,
            "validation": "single_object",
            "signature_valid": signature_valid,
            "record_valid": record_valid,
            "head_valid": head_valid,
            "signature": signature,
            "object_path": object_path,
            "length_before": before_length,
            "length_after": after_length,
            "ledger_head": disk_head,
            "merkle_root": merkle_root,
        }

    def commit_action(self, tool_id: str, payload: dict) -> bool:
        if not self.has_trustchain():
            return False
        payload = self._enrich_payload_from_governed_session(payload)
        if not _artifact_file_partition_valid(payload):
            return False
        if not self._policy_allows(tool_id, payload):
            return False

        from apatch.runtime.atomic_io import exclusive_file_lock

        self._last_commit_evidence = None
        with exclusive_file_lock(self._ledger_lock_path()):
            before_head = self.ledger_head()
            try:
                from trustchain import TrustChain

                tc = TrustChain(self._tc_config())
                chain = getattr(tc, "chain", None)
                before_length = getattr(chain, "length", None)
                signed = tc.sign(tool_id=tool_id, data=payload)
                self._last_commit_evidence = self._validate_python_commit(
                    tc,
                    signed,
                    tool_id=tool_id,
                    payload=payload,
                    before_length=before_length if isinstance(before_length, int) else None,
                )
                ok = bool(self._last_commit_evidence.get("persisted"))
            except ImportError:
                ok = self._commit_via_subprocess(tool_id, payload, before_head=before_head)
            except Exception:
                ok = self._commit_via_subprocess(tool_id, payload, before_head=before_head)

        if ok:
            self._signature_index = None
            self._maybe_push_to_platform(tool_id, payload)
            evidence = self.last_commit_evidence
            session_id = str(
                payload.get("governed_session_id") or payload.get("session_id") or ""
            ).strip()
            files = payload.get("files")
            if isinstance(files, dict) and files:
                try:
                    from apatch.enforcement import record_notarized_files

                    record_notarized_files(
                        self.workspace_root,
                        files,
                        signature=evidence.get("signature"),
                        object_path=evidence.get("object_path"),
                        ledger_length=evidence.get("length_after"),
                        ledger_head=evidence.get("ledger_head"),
                        governed_session_id=session_id or None,
                    )
                except Exception:
                    pass
            if session_id and (
                tool_id == "apatch_attest"
                or (
                    tool_id in ("apatch", "apatch_expert")
                    and payload.get("action") == "attest"
                )
            ):
                try:
                    from apatch.enforcement import record_session_attested

                    record_session_attested(
                        self.workspace_root,
                        session_id,
                        signature=evidence.get("signature"),
                        object_path=evidence.get("object_path"),
                    )
                except Exception:
                    pass
        elif payload.get("files"):
            try:
                from apatch.enforcement import is_enforcement_enabled

                if is_enforcement_enabled(self.workspace_root):
                    print(
                        "[apatch] REJECTED: TrustChain commit failed — "
                        "new Ed25519 object did not produce a valid O(1) receipt",
                        file=sys.stderr,
                    )
            except Exception:
                pass
        return ok

    def _maybe_push_to_platform(self, tool_id: str, payload: dict) -> None:
        """Best-effort push to Platform when APATCH_PLATFORM_* env is set."""
        try:
            from apatch.platform_client import platform_config_from_env, push_step
            from cryptography.hazmat.primitives.serialization import load_pem_private_key

            cfg = platform_config_from_env()
            if not cfg:
                return
            with open(cfg["key_path"], "rb") as f:
                private_key = load_pem_private_key(f.read(), password=None)
            meta: dict = {"source": "apatch", "workspace": self.workspace_root}
            if cfg.get("tenant_id"):
                meta["tenant_id"] = cfg["tenant_id"]
            result = push_step(
                tool=tool_id,
                data=payload,
                base_url=cfg["base_url"],
                agent_id=cfg["agent_id"],
                private_key=private_key,
                metadata=meta,
                return_op_id=True,
            )
            # Record the external anchor so CI can prove inclusion later
            # (RFP-005 §5.10). result is the op_id string on success.
            if isinstance(result, str):
                from apatch.inclusion import record_inclusion

                record_inclusion(
                    self.workspace_root, op_id=result, tool=tool_id
                )
        except Exception:
            pass

    def _maybe_push_revert_to_platform(self, target_op_id: str, reason: str) -> None:
        try:
            from apatch.platform_client import platform_config_from_env, push_revert
            from cryptography.hazmat.primitives.serialization import load_pem_private_key

            cfg = platform_config_from_env()
            if not cfg:
                return
            with open(cfg["key_path"], "rb") as f:
                private_key = load_pem_private_key(f.read(), password=None)
            push_revert(
                target_op_id=target_op_id,
                reason=reason,
                base_url=cfg["base_url"],
                agent_id=cfg["agent_id"],
                private_key=private_key,
            )
        except Exception:
            pass

    def _commit_via_subprocess(
        self,
        tool_id: str,
        payload: dict,
        *,
        before_head: Optional[str] = None,
    ) -> bool:
        """Fallback signed commit with an O(1) receipt from the child process."""
        script = (
            "import json, os\n"
            "from trustchain import TrustChain, TrustChainConfig\n"
            "chain_dir = os.environ['TC_CHAIN_DIR']\n"
            "tool_id = os.environ['TC_TOOL_ID']\n"
            "payload = json.loads(os.environ['TC_PAYLOAD'])\n"
            "kwargs = dict(enable_chain=True, chain_storage='file', chain_dir=chain_dir)\n"
            "kp = os.environ.get('APATCH_AGENT_KEY'); aid = os.environ.get('APATCH_AGENT_ID')\n"
            "if kp and aid and os.path.isfile(kp):\n"
            "    try:\n"
            "        from apatch.trust_identity import _PemKeyProvider\n"
            "        kwargs['key_provider'] = _PemKeyProvider(kp, key_id=aid)\n"
            "    except Exception:\n"
            "        pass\n"
            "cfg = TrustChainConfig(**kwargs)\n"
            "tc = TrustChain(cfg)\n"
            "signed = tc.sign(tool_id=tool_id, data=payload)\n"
            "print(json.dumps({'signature': signed.signature, 'length': tc.chain.length}))\n"
        )
        env = os.environ.copy()
        env["TC_CHAIN_DIR"] = self.trustchain_dir
        env["TC_TOOL_ID"] = tool_id
        env["TC_PAYLOAD"] = json.dumps(payload)
        extra_path = os.environ.get("APATCH_TC_PYTHONPATH")
        if extra_path:
            env["PYTHONPATH"] = (extra_path + os.pathsep + env.get("PYTHONPATH", "")).rstrip(os.pathsep)
        py_exe = os.environ.get("APATCH_TC_PYTHON") or sys.executable
        try:
            res = subprocess.run(
                [py_exe, "-c", script],
                capture_output=True,
                text=True,
                env=env,
            )
        except OSError:
            return False
        if res.returncode != 0:
            return False
        try:
            receipt = json.loads([line for line in res.stdout.splitlines() if line.strip()][-1])
        except (IndexError, json.JSONDecodeError):
            receipt = {}
        signature = receipt.get("signature")
        length = receipt.get("length")
        after_head = self.ledger_head()
        object_path = None
        record_valid = False
        if isinstance(length, int):
            object_path = os.path.join("objects", f"op_{length:04d}.json")
            absolute = os.path.join(self.trustchain_dir or "", object_path)
            try:
                with open(absolute, encoding="utf-8") as f:
                    stored = json.load(f)
                value = stored.get("value") if isinstance(stored.get("value"), dict) else stored
                record_valid = bool(
                    value.get("tool") == tool_id
                    and value.get("data") == payload
                    and value.get("signature") == signature
                )
            except (OSError, json.JSONDecodeError, AttributeError):
                record_valid = False
        persisted = bool(
            signature and after_head == signature and after_head != before_head and record_valid
        )
        self._last_commit_evidence = {
            "persisted": persisted,
            "validation": "single_object_subprocess",
            "signature_valid": bool(signature),
            "record_valid": record_valid,
            "head_valid": after_head == signature,
            "signature": signature,
            "object_path": object_path,
            "length_before": None,
            "length_after": length,
            "ledger_head": after_head,
            "merkle_root": None,
        }
        return persisted

    def bind_physical_backup(self, checkpoint_name: str, backup_mgr: "BackupManager") -> None:
        """Associate a BackupManager session with a TrustChain checkpoint name."""
        self._physical_backups[checkpoint_name] = backup_mgr

    def begin_mutating_session(
        self,
        label: str = "apatch_strip",
        *,
        source_files: Optional[List[str]] = None,
        backup_mgr: Optional["BackupManager"] = None,
    ) -> Optional[str]:
        """Create checkpoint + optional physical backups before a destructive apatch op."""
        import time

        sess_id = f"{label}_{time.time_ns()}_{os.getpid()}"
        mgr = backup_mgr
        if source_files and mgr is None:
            from apatch.backup import BackupManager

            mgr = BackupManager(self.workspace_root, session_id=sess_id)
            for path in source_files:
                mgr.create_backup(os.path.abspath(path))

        if self.has_trustchain():
            self.ensure_head()
            if not self.create_checkpoint(sess_id) and mgr is None:
                return None
        elif mgr is None:
            return None

        if mgr is not None:
            self.bind_physical_backup(sess_id, mgr)
            if source_files:
                self.commit_checkpoint_files(sess_id, list(source_files))
        return sess_id

    def rollback_session(
        self,
        checkpoint_name: str,
        backup_mgr: Optional["BackupManager"] = None,
    ) -> bool:
        """Alias: checkpoint rollback restores files + TrustChain HEAD."""
        if backup_mgr is not None:
            self.bind_physical_backup(checkpoint_name, backup_mgr)
        return self.rollback_checkpoint(checkpoint_name)

    def commit_strip_result(
        self,
        *,
        source_file: str,
        parent_imports: list,
        exported_meta: list,
        checkpoint_name: Optional[str] = None,
    ) -> bool:
        payload = {
            "action": "strip",
            "checkpoint": checkpoint_name,
            "source_file": os.path.abspath(source_file),
            "parent_imports": parent_imports,
            "extracted_blocks": [
                {
                    "filename": b["filename"],
                    "export_path": b["export_path"],
                    "sha256": b["sha256"],
                    "line_range": b["line_range"],
                }
                for b in exported_meta
            ],
        }
        return self.commit_action("apatch", payload)
