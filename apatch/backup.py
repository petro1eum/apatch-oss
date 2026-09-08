import os
import shutil
import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple


class BackupError(Exception):
    """Raised when a pre-mutation backup cannot be created, so the caller
    aborts the write instead of silently overwriting an un-backed-up file."""


class BackupManager:
    """
    BackupManager handles transactional file backups before modification,
    allowing one-click rollbacks of applied session patches.
    """
    def __init__(
        self,
        target_dir: str,
        session_id: Optional[str] = None,
        *,
        governed_session_id: Optional[str] = None,
    ):
        self.target_dir = os.path.abspath(target_dir)
        self.apatch_dir = os.path.join(self.target_dir, ".apatch")
        self.backups_dir = os.path.join(self.apatch_dir, "backups")
        
        if session_id:
            self.session_id = session_id
        else:
            self.session_id = f"session_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{os.getpid()}"

        if governed_session_id is None:
            try:
                from apatch.lane_context import current_tool_governed_session_id

                governed_session_id = current_tool_governed_session_id(self.target_dir)
            except Exception:
                governed_session_id = None
        self.governed_session_id = str(governed_session_id or "") or None
            
        self.session_path = os.path.join(self.backups_dir, self.session_id)
        self.metadata_path = os.path.join(self.session_path, "metadata.json")
        self.backed_up_files = set()
        
    def _load_existing_backups(self) -> List[Dict[str, str]]:
        if os.path.exists(self.metadata_path):
            try:
                with open(self.metadata_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    backups = data.get("backups", [])
                    for b in backups:
                        self.backed_up_files.add(os.path.abspath(b["original_file"]))
                    return backups
            except Exception:
                pass
        return []

    def _save_metadata(self, backups: List[Dict[str, str]]):
        from apatch.runtime.atomic_io import (
            atomic_write_json,
            exclusive_file_lock,
            read_json_file,
        )

        os.makedirs(self.session_path, exist_ok=True)
        with exclusive_file_lock(self.metadata_path):
            current = read_json_file(self.metadata_path, {})
            current_owner = str(current.get("governed_session_id") or "") or None
            if (
                current_owner
                and self.governed_session_id
                and current_owner != self.governed_session_id
            ):
                raise BackupError(
                    "Backup checkpoint ownership cannot be reassigned from "
                    f"{current_owner} to {self.governed_session_id}."
                )
            owner = self.governed_session_id or current_owner
            self.governed_session_id = owner
            data = {
                "session_id": self.session_id,
                "governed_session_id": owner,
                "timestamp": datetime.now().isoformat(),
                "backups": backups,
            }
            atomic_write_json(self.metadata_path, data)

    def create_backup(self, file_path: str, step_index: int = 0, action_type: str = "REPLACE") -> bool:
        """
        Creates a backup of the target file under the session folder
        if it hasn't been backed up during this specific step yet.
        """
        abs_file = os.path.abspath(file_path)
        
        # Ensure metadata is loaded
        backups = self._load_existing_backups()
        
        # Check if already backed up in this specific step
        for b in backups:
            if os.path.abspath(b["original_file"]) == abs_file and b.get("step_index") == step_index:
                return True
                
        try:
            os.makedirs(self.session_path, exist_ok=True)
            
            if action_type == "CREATE":
                # File does not exist yet. Record its creation.
                backups.append({
                    "original_file": abs_file,
                    "backup_file": None,
                    "step_index": step_index,
                    "action": "created"
                })
                self.backed_up_files.add(abs_file)
                self._save_metadata(backups)
                return True
                
            # For REPLACE or DELETE, the file should exist
            if not os.path.exists(abs_file):
                return False
                
            backup_filename = f"backup_{len(backups) + 1}_{os.path.basename(file_path)}.bak"
            backup_file_path = os.path.join(self.session_path, backup_filename)
            
            shutil.copy2(abs_file, backup_file_path)
            
            backups.append({
                "original_file": abs_file,
                "backup_file": backup_filename,
                "step_index": step_index,
                "action": "deleted" if action_type == "DELETE" else "modified"
            })
            self.backed_up_files.add(abs_file)
            self._save_metadata(backups)
            return True
        except Exception as e:
            raise BackupError(
                f"Failed to back up {abs_file} before mutation: {e}"
            ) from e

    def rollback_step(self, step_index: int) -> List[str]:
        """
        Rolls back all changes applied in a specific step index.
        Physically deletes files created in this step, and restores modified/deleted ones.
        """
        backups = self._load_existing_backups()
        step_entries = [b for b in backups if b.get("step_index") == step_index]
        if not step_entries:
            return []
            
        restored = []
        # Revert in reverse chronological order
        for b in reversed(step_entries):
            orig = b["original_file"]
            action = b.get("action", "modified")
            
            if action == "created":
                if os.path.exists(orig):
                    try:
                        os.remove(orig)
                        restored.append(orig)
                    except Exception:
                        pass
            else:
                bak_file = os.path.join(self.session_path, b["backup_file"])
                if os.path.exists(bak_file):
                    try:
                        os.makedirs(os.path.dirname(orig), exist_ok=True)
                        shutil.copy2(bak_file, orig)
                        restored.append(orig)
                    except Exception:
                        pass
                        
        # Keep only non-reverted entries in metadata
        remaining_backups = [b for b in backups if b.get("step_index") != step_index]
        self._save_metadata(remaining_backups)
        
        # Clear backed_up_files set and reload
        self.backed_up_files.clear()
        for b in remaining_backups:
            self.backed_up_files.add(os.path.abspath(b["original_file"]))
            
        return restored

    def restore_file(self, file_path: str) -> bool:
        """
        Restores a single file back to its pre-session state.
        """
        abs_file = os.path.abspath(file_path)
        backups = self._load_existing_backups()
        # Oldest first. A file edited more than once in a session has a backup
        # per edit, and only the first one holds the state the session started
        # from; restoring the newest would leave the file half rolled back,
        # matching neither the session's result nor what preceded it.
        for b in backups:
            if os.path.abspath(b["original_file"]) == abs_file:
                if b.get("action") == "created":
                    if os.path.exists(abs_file):
                        try:
                            os.remove(abs_file)
                            return True
                        except Exception:
                            pass
                else:
                    bak_file = os.path.join(self.session_path, b["backup_file"])
                    if os.path.exists(bak_file):
                        try:
                            shutil.copy2(bak_file, abs_file)
                            return True
                        except Exception:
                            pass
        return False

    @staticmethod
    def get_available_sessions(target_dir: str) -> List[Tuple[str, str]]:
        """
        Returns a list of tuples containing (session_id, timestamp) sorted chronologically (latest first).
        """
        backups_dir = os.path.join(os.path.abspath(target_dir), ".apatch", "backups")
        if not os.path.exists(backups_dir):
            return []
            
        sessions = []
        for name in os.listdir(backups_dir):
            sess_path = os.path.join(backups_dir, name)
            meta_path = os.path.join(sess_path, "metadata.json")
            if os.path.isdir(sess_path) and os.path.exists(meta_path):
                try:
                    with open(meta_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        sessions.append((name, data.get("timestamp", "")))
                except Exception:
                    pass
                    
        # Sort by timestamp or session folder name descending
        sessions.sort(key=lambda x: x[1] or x[0], reverse=True)
        return sessions

    @staticmethod
    def list_session_files(target_dir: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        """Preview which files a rollback session would restore (no disk writes)."""
        target_dir = os.path.abspath(target_dir)
        sessions = BackupManager.get_available_sessions(target_dir)
        if not sessions:
            raise ValueError("No patch sessions available to rollback.")

        target_session = session_id if session_id else sessions[0][0]
        sess_path = os.path.join(target_dir, ".apatch", "backups", target_session)
        meta_path = os.path.join(sess_path, "metadata.json")
        if not os.path.exists(meta_path):
            raise ValueError(f"Session metadata not found for: {target_session}")

        with open(meta_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        files = []
        for b in data.get("backups", []):
            files.append({
                "path": b.get("original_file"),
                "action": b.get("action", "modified"),
                "step_index": b.get("step_index"),
            })
        return {
            "session_id": target_session,
            "governed_session_id": data.get("governed_session_id"),
            "timestamp": data.get("timestamp", ""),
            "files": files,
            "count": len(files),
            "available_sessions": [
                {"session_id": sid, "timestamp": ts} for sid, ts in sessions
            ],
        }

    @staticmethod
    def rollback_session(target_dir: str, session_id: Optional[str] = None) -> List[str]:
        """
        Restores all files modified in the specified session (or the last session if None).
        Returns a list of restored absolute file paths.
        """
        target_dir = os.path.abspath(target_dir)
        sessions = BackupManager.get_available_sessions(target_dir)
        if not sessions:
            raise ValueError("No patch sessions available to rollback.")
            
        target_session = session_id if session_id else sessions[0][0]
        sess_path = os.path.join(target_dir, ".apatch", "backups", target_session)
        meta_path = os.path.join(sess_path, "metadata.json")
        
        if not os.path.exists(meta_path):
            raise ValueError(f"Session metadata not found for: {target_session}")
            
        with open(meta_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        restored = []
        for b in reversed(data.get("backups", [])):
            orig = b["original_file"]
            action = b.get("action", "modified")
            if action == "created":
                if os.path.exists(orig):
                    try:
                        os.remove(orig)
                        restored.append(orig)
                    except Exception:
                        pass
            else:
                backup_file_name = b.get("backup_file")
                if backup_file_name:
                    bak_file = os.path.join(sess_path, backup_file_name)
                    if os.path.exists(bak_file):
                        os.makedirs(os.path.dirname(orig), exist_ok=True)
                        shutil.copy2(bak_file, orig)
                        restored.append(orig)
                
        # Clean up session folder
        try:
            shutil.rmtree(sess_path)
            # If the backups folder or .apatch folder is now empty, clean them too
            backups_dir = os.path.dirname(sess_path)
            if not os.listdir(backups_dir):
                os.rmdir(backups_dir)
            apatch_dir = os.path.dirname(backups_dir)
            if not os.listdir(apatch_dir):
                os.rmdir(apatch_dir)
        except Exception:
            pass
            
        return restored
