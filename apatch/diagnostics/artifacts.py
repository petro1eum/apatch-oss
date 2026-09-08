"""Persist session diagnostics under .apatch/diagnostics/."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from apatch.diagnostics.schema import SCHEMA_VERSION


def write_session_diagnostics(
    target_dir: str,
    session_id: str,
    diagnostics: List[Dict[str, Any]],
    *,
    write_artifacts: bool = True,
) -> Optional[Path]:
    if not write_artifacts or not session_id:
        return None
    root = Path(target_dir).resolve()
    out_dir = root / ".apatch" / "diagnostics"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{session_id}.json"
    doc = {
        "schema_version": SCHEMA_VERSION,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        "diagnostic_count": len(diagnostics),
        "diagnostics": diagnostics,
    }
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path