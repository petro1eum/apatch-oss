"""Human- and agent-facing rejection prompts for unnotarized code changes."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

REASON_LABELS = {
    "never_notarized": "файл никогда не был нотаризован через apatch + TrustChain",
    "sha256_mismatch": "содержимое на диске не совпадает с последней Ed25519-нотаризацией",
    "file_deleted_without_notarized_delete": "файл удалён без подписанного delete в TrustChain",
    "no_trustchain": "попытка apply с отключённым TrustChain",
    "notarization_failed": "apatch записал файлы, но блок с подписью в .trustchain/ не создан",
    "no_signed_blocks": "в .trustchain/ нет ни одного подписанного block_*.json / objects/*.json",
}

POLICY_SUMMARY = (
    "Политика репозитория: любое изменение исходного кода (ИИ, скрипты, агент) "
    "обязано сопровождаться криптографической подписью Ed25519 в .trustchain/ "
    "(chain/block_*.json или objects/*.json). Правки без подписи — нелегитимны и блокируются."
)


def _reason_label(reason: str) -> str:
    return REASON_LABELS.get(reason, reason)


def build_rejection_prompt(
    *,
    reason: str,
    paths: Optional[List[str]] = None,
    detail: Optional[str] = None,
    violations: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Full rejection text for terminal, MCP JSON, and agent context."""
    path_lines: List[str] = []
    if violations:
        for v in violations[:12]:
            p = v.get("path", "?")
            r = _reason_label(str(v.get("reason", reason)))
            hint = v.get("hint") or ""
            path_lines.append(f"  • {p} — {r}" + (f" ({hint})" if hint else ""))
        if len(violations) > 12:
            path_lines.append(f"  • … и ещё {len(violations) - 12} файл(ов)")
    elif paths:
        for p in paths[:12]:
            path_lines.append(f"  • {p}")
        if len(paths) > 12:
            path_lines.append(f"  • … и ещё {len(paths) - 12} файл(ов)")

    files_block = "\n".join(path_lines) if path_lines else "  • (см. violations в JSON-ответе)"

    extra = f"\nДетали: {detail}\n" if detail else ""

    return f"""╔══════════════════════════════════════════════════════════════════╗
║  ОТКЛОНЕНО — нелегитимное изменение исходного кода               ║
╚══════════════════════════════════════════════════════════════════╝

{POLICY_SUMMARY}

Ваш запрос отклонён: {_reason_label(reason)}.
Обход TrustChain (sed / awk / search_replace / python re / echo > file) здесь не работает.

Затронутые файлы:
{files_block}
{extra}
Единственный легальный путь:
  1. apatch_doctor(target_dir=".")
  2. apatch_generate → apatch_apply_session (цикл, пока continue=false)
  3. Проверить: в .trustchain/chain/ или .trustchain/objects/ появился новый
     подписанный блок (Ed25519), не только genesis/config
  4. apatch_verify_notarization(staged=true) перед git commit

Пока нет подписи — правка не существует для этой платформы. Исправьте процесс, не обходите его.
"""


def wrap_rejection_result(
    result: Dict[str, Any],
    *,
    reason: str,
    detail: Optional[str] = None,
) -> Dict[str, Any]:
    """Attach machine + human rejection fields to a verify/apply response."""
    violations = result.get("violations") or []
    paths = [str(v.get("path")) for v in violations if v.get("path")]
    prompt = build_rejection_prompt(
        reason=reason,
        paths=paths or None,
        detail=detail,
        violations=violations,
    )
    out = dict(result)
    out["ok"] = False
    out["rejected"] = True
    out["rejection_reason"] = reason
    out["rejection_title"] = "ОТКЛОНЕНО — нелегитимное изменение без Ed25519 подписи"
    out["rejection_prompt"] = prompt
    out["agent_prompt"] = prompt
    out["policy"] = POLICY_SUMMARY
    out["legal_path"] = [
        "apatch_doctor",
        "apatch_generate → apatch_apply_session",
        "verify .trustchain/chain/block_*.json or objects/*.json exists",
        "apatch_verify_notarization(staged=true)",
    ]
    out["forbidden"] = ["sed", "awk", "search_replace", "python re", "shell redirect", "no_trustchain"]
    return out


def apply_blocked_message(reason: str, *, detail: Optional[str] = None) -> str:
    return build_rejection_prompt(reason=reason, detail=detail)
