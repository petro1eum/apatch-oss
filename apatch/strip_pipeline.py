"""Orchestration for apatch strip: backup, apply, export, report, verify, rollback."""

from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from apatch.converters.registry import get_converter
from apatch.converters.ts_module import classify_strip_shape
from apatch.import_paths import normalize_ts_import_line, resolve_consumer_root, workspace_ts_import
from apatch.dangling_refs import find_dangling_references, merge_dangling_reports
from apatch.imports_resolver import resolve_parent_imports
from apatch.manifest_validator import ManifestValidationError, assert_valid_manifest, validate_manifest
from apatch.strip import (
    StripResult,
    StripSpec,
    apply_strips,
    default_export_ext,
    load_strip_manifest,
)
from apatch.strip_cache import cache_key, load_cached, save_cached
from apatch.tool_paths import build_subprocess_env, run_shell_verify
from apatch.trustchain_helper import TrustChainHelper
from apatch.wiring_profiles import (
    _pascal_label,
    build_cpp_wiring_hints,
    build_integration_hints,
    build_typescript_wiring_hints,
    detect_profile,
    emit_wiring_markdown,
)


@dataclass
class StripPipelineConfig:
    file_path: str
    specs: Sequence[StripSpec]
    dry_run: bool = False
    out_dir: Optional[str] = None
    export_ext: Optional[str] = None
    export_filename: Optional[str] = None
    to_native: Optional[str] = None
    to_module: Optional[str] = None
    module_out_dir: Optional[str] = None
    native_out_dir: Optional[str] = None
    post_hook: Optional[str] = None
    strict: bool = False
    strict_overlap: bool = False
    strict_dangling: bool = False
    no_trustchain: bool = False
    emit_wiring: Optional[str] = None
    verify_cmd: Optional[str] = None
    profile: str = "auto"
    as_json: bool = False
    use_cache: bool = True
    manifest_path: Optional[str] = None
    auto_wire: bool = False
    emit_barrel: Optional[str] = None
    skip_tc_commit: bool = False
    on_message: Optional[Callable[[str, str], None]] = None


@dataclass
class StripPipelineResult:
    ok: bool
    results: List[StripResult] = field(default_factory=list)
    exported_meta: List[Dict[str, Any]] = field(default_factory=list)
    report_path: Optional[str] = None
    report_data: Dict[str, Any] = field(default_factory=dict)
    exit_code: int = 0
    errors: List[str] = field(default_factory=list)
    pipeline_warnings: List[str] = field(default_factory=list)
    tc_checkpoint: Optional[str] = None


def _msg(cfg: StripPipelineConfig, level: str, text: str) -> None:
    if cfg.on_message:
        cfg.on_message(level, text)


def _strip_cpp_else_branches(content: str) -> str:
    out: List[str] = []
    pos = 0
    while True:
        m = re.search(r"^\s*#else\b", content[pos:], flags=re.MULTILINE)
        if not m:
            out.append(content[pos:])
            break
        out.append(content[pos : pos + m.start()])
        tail = content[pos + m.start() :]
        em = re.search(r"^\s*#endif\b", tail, flags=re.MULTILINE)
        if not em:
            out.append(tail)
            break
        pos = pos + m.start() + em.end()
    return "".join(out)


def check_native_duplicates(search_dir: str) -> Dict[str, List[str]]:
    registrations: Dict[str, List[str]] = {}
    patterns = ["*.cpp", "*.hpp", "*.h"]
    cpp_files: List[str] = []
    for pat in patterns:
        cpp_files.extend(glob.glob(os.path.join(search_dir, pat)))
    for cpp in cpp_files:
        try:
            with open(cpp, "r", encoding="utf-8") as f:
                content = _strip_cpp_else_branches(f.read())
            matches = re.findall(r'register_native\s*\(\s*["\']([^"\']+)["\']', content)
            base = os.path.basename(cpp)
            for m, cnt in Counter(matches).items():
                if cnt > 1:
                    registrations.setdefault(m, []).append(f"{base} (×{cnt})")
                elif m not in registrations:
                    registrations[m] = []
                if cnt == 1 and base not in registrations[m]:
                    registrations[m].append(base)
        except Exception:
            pass
    return {
        name: files
        for name, files in registrations.items()
        if len(files) > 1 or any("×" in f for f in files)
    }


def resolve_profile(cfg: StripPipelineConfig) -> str:
    if cfg.profile and cfg.profile != "auto":
        return cfg.profile
    detected = detect_profile(cfg.file_path)
    if cfg.to_native:
        return "cpp"
    if cfg.to_module:
        return "typescript"
    return detected


def run_strip_pipeline(
    cfg: StripPipelineConfig,
    *,
    tc_helper: Optional[TrustChainHelper] = None,
    tc_checkpoint: Optional[str] = None,
    rollback_fn: Optional[Callable[..., None]] = None,
) -> StripPipelineResult:
    file_path = cfg.file_path
    specs = list(cfg.specs)
    profile = resolve_profile(cfg)
    workspace = resolve_consumer_root(file_path)
    cfg = _normalize_strip_paths(cfg, workspace)

    pipeline_warnings: List[str] = []
    if cfg.to_module and not cfg.out_dir:
        cfg.out_dir = "extracted"
        pipeline_warnings.append(
            "to_module strip: defaulted out_dir to 'extracted' (module codegen + auto_wire)"
        )

    if cfg.export_ext is None:
        export_ext = default_export_ext(file_path)
    else:
        export_ext = cfg.export_ext

    try:
        orig_content = Path(file_path).read_text(encoding="utf-8")
    except Exception as e:
        return StripPipelineResult(ok=False, errors=[str(e)], exit_code=1)

    lines = orig_content.splitlines(keepends=True)

    if cfg.strict_overlap:
        overlap_errors = validate_manifest(lines, specs)
        if overlap_errors:
            return StripPipelineResult(ok=False, errors=overlap_errors, exit_code=1)

    fingerprint = hashlib.sha256(
        json.dumps([s.__dict__ for s in specs], sort_keys=True).encode()
    ).hexdigest()[:16]
    if cfg.dry_run and cfg.use_cache and cfg.as_json:
        key = cache_key(file_path, cfg.manifest_path, fingerprint)
        cached = load_cached(workspace, key)
        if cached:
            return StripPipelineResult(
                ok=True,
                exported_meta=cached.get("exported_meta", []),
                report_data=cached,
                exit_code=0,
            )

    parent_imports = resolve_parent_imports(file_path, orig_content, workspace)

    if not cfg.dry_run and tc_helper is None and not cfg.no_trustchain:
        tc_helper = TrustChainHelper(workspace, auto_init=True)
        if tc_checkpoint is None:
            tc_checkpoint = tc_helper.begin_mutating_session("apatch_strip", source_files=[file_path])

    _, results = apply_strips(file_path, specs, dry_run=cfg.dry_run)

    if cfg.out_dir:
        os.makedirs(cfg.out_dir, exist_ok=True)

    exported_meta: List[Dict[str, Any]] = []
    written_filenames: set = set()

    # Parent content after strip (for dangling analysis)
    if cfg.dry_run:
        sim_lines = list(lines)
        from apatch.strip import apply_strip

        ordered = sorted(
            enumerate(specs),
            key=lambda x: _safe_find(sim_lines, x[1].start),
            reverse=True,
        )
        for _, spec in ordered:
            apply_strip(sim_lines, spec)
        parent_remaining = "".join(sim_lines)
    else:
        parent_remaining = Path(file_path).read_text(encoding="utf-8")

    for r in results:
        label = r.spec.label or r.spec.start[:40]
        if not r.ok:
            if not cfg.dry_run and rollback_fn:
                rollback_fn(tc_helper, tc_checkpoint, file_path, exported_meta)
            return StripPipelineResult(
                ok=False,
                results=results,
                errors=[r.error or f"strip failed: {label}"],
                exit_code=1,
            )

        if cfg.out_dir and r.removed_content:
            meta = _export_block(
                cfg, r, label, export_ext, written_filenames, profile, parent_imports
            )
            if meta:
                dangling = find_dangling_references(r.removed_content, parent_remaining)
                meta["dangling_references"] = dangling
                if r.boundary_warnings:
                    meta["boundary_warnings"] = r.boundary_warnings
                try:
                    from apatch.boundary_ranker import assess_until_marker

                    meta["boundary_assessment"] = assess_until_marker(
                        file_path, lines, r.spec.start, r.spec.until
                    )
                except Exception:
                    pass
                exported_meta.append(meta)

    if cfg.strict_dangling:
        all_dangling = merge_dangling_reports(exported_meta)
        if all_dangling:
            names = ", ".join(d["name"] for d in all_dangling)
            err = f"dangling references in parent after strip: {names}"
            if not cfg.dry_run and rollback_fn:
                rollback_fn(tc_helper, tc_checkpoint, file_path, exported_meta)
            return StripPipelineResult(
                ok=False,
                results=results,
                exported_meta=exported_meta,
                errors=[err],
                exit_code=1,
            )

    report_data: Dict[str, Any] = {}
    report_path: Optional[str] = None

    if cfg.out_dir and exported_meta and not cfg.dry_run:
        report_path, report_data = _write_report(
            cfg, file_path, parent_imports, results, exported_meta, profile
        )
        conv_err = _run_native_conversion(cfg, exported_meta, report_path, tc_helper, tc_checkpoint, rollback_fn)
        if conv_err:
            return conv_err
        _run_module_conversion(cfg, exported_meta, report_data, orig_content)
        if cfg.emit_barrel:
            from apatch.barrel_export import emit_barrel_exports

            barrel_result = emit_barrel_exports(cfg.emit_barrel, exported_meta)
            report_data.setdefault("barrel_export", barrel_result)
        if cfg.auto_wire and cfg.to_module:
            from apatch.auto_wire import apply_auto_wire

            stub_replaces = [s.replace for s in specs if s.replace]
            wire_result = apply_auto_wire(
                file_path,
                exported_meta,
                specs_replace=stub_replaces,
                workspace=workspace,
            )
            report_data["auto_wire"] = wire_result
            if not wire_result.get("ok"):
                if rollback_fn:
                    rollback_fn(tc_helper, tc_checkpoint, file_path, exported_meta, report_path)
                return StripPipelineResult(
                    ok=False,
                    errors=[wire_result.get("error", "auto_wire failed")],
                    exit_code=1,
                )
            if not cfg.dry_run:
                from apatch.imports_resolver import prune_unused_imports

                parent_text = Path(file_path).read_text(encoding="utf-8")
                pruned = prune_unused_imports(parent_text)
                if pruned != parent_text:
                    Path(file_path).write_text(pruned, encoding="utf-8")
                    report_data["parent_import_prune"] = True
        if report_path and os.path.exists(report_path):
            with open(report_path, "r", encoding="utf-8") as f:
                report_data = json.load(f)
        if cfg.emit_wiring:
            _emit_wiring_file(cfg, file_path, exported_meta, profile)
        if cfg.post_hook:
            _run_post_hooks(cfg, exported_meta, report_path)
        if tc_helper and tc_helper.has_trustchain() and not cfg.skip_tc_commit:
            try:
                tc_helper.commit_strip_result(
                    source_file=file_path,
                    parent_imports=parent_imports,
                    exported_meta=exported_meta,
                    checkpoint_name=tc_checkpoint,
                )
            except Exception:
                pass

    if cfg.verify_cmd and not cfg.dry_run and exported_meta:
        from apatch.imports_resolver import prune_unused_imports

        parent_text = Path(file_path).read_text(encoding="utf-8")
        pruned = prune_unused_imports(parent_text)
        if pruned != parent_text:
            Path(file_path).write_text(pruned, encoding="utf-8")
            report_data.setdefault("parent_import_prune", True)

    if cfg.verify_cmd and not cfg.dry_run:
        v_ok, v_err = _run_verify(cfg.verify_cmd, workspace)
        if not v_ok:
            _attach_verify_failure_hints(exported_meta, v_err)
            if report_path and report_data:
                report_data["verify_failure"] = v_err
                report_data["integration_hints"] = report_data.get("integration_hints", {})
                report_data["integration_hints"]["verify_failure_hints"] = _verify_tsc_hints(v_err)
                Path(report_path).write_text(
                    json.dumps(report_data, indent=2, ensure_ascii=False), encoding="utf-8"
                )
            if rollback_fn:
                rollback_fn(tc_helper, tc_checkpoint, file_path, exported_meta, report_path)
            return StripPipelineResult(
                ok=False,
                results=results,
                exported_meta=exported_meta,
                report_path=report_path,
                report_data=report_data,
                errors=[v_err or "verify failed"],
                exit_code=1,
                tc_checkpoint=tc_checkpoint,
            )

    if cfg.dry_run and cfg.use_cache and cfg.as_json and cfg.out_dir:
        payload = {
            "exported_meta": exported_meta,
            "dangling_references": merge_dangling_reports(exported_meta),
        }
        save_cached(workspace, cache_key(file_path, cfg.manifest_path, fingerprint), payload)

    return StripPipelineResult(
        ok=True,
        results=results,
        exported_meta=exported_meta,
        report_path=report_path,
        report_data=report_data,
        exit_code=0,
        tc_checkpoint=tc_checkpoint,
        pipeline_warnings=pipeline_warnings,
    )


def _resolve_in_workspace(workspace: str, path: str) -> str:
    if os.path.isabs(path):
        return os.path.normpath(path)
    return os.path.normpath(os.path.join(workspace, path))


def _module_file_ext(to_module: Optional[str]) -> str:
    return ".tsx" if to_module == "component" else ".ts"


def _normalize_strip_paths(cfg: StripPipelineConfig, workspace: str) -> StripPipelineConfig:
    if cfg.out_dir:
        cfg.out_dir = _resolve_in_workspace(workspace, cfg.out_dir)
    if cfg.module_out_dir:
        cfg.module_out_dir = _resolve_in_workspace(workspace, cfg.module_out_dir)
    if cfg.native_out_dir:
        cfg.native_out_dir = _resolve_in_workspace(workspace, cfg.native_out_dir)
    if cfg.emit_wiring:
        cfg.emit_wiring = _resolve_in_workspace(workspace, cfg.emit_wiring)
    if cfg.emit_barrel:
        cfg.emit_barrel = _resolve_in_workspace(workspace, cfg.emit_barrel)
    return cfg


def _safe_find(lines: Sequence[str], needle: str) -> int:
    from apatch.strip import _find_line

    try:
        return _find_line(lines, needle)
    except ValueError:
        return -1


def _export_block(
    cfg: StripPipelineConfig,
    r: StripResult,
    label: str,
    export_ext: str,
    written_filenames: set,
    profile: str,
    parent_imports: List[str],
) -> Optional[Dict[str, Any]]:
    out_dir = cfg.out_dir
    assert out_dir

    if r.spec.export:
        filename = r.spec.export
        is_explicit = True
    else:
        file_match = re.search(
            r"([a-zA-Z0-9_-]+\.[a-zA-Z0-9]{1,5})", r.spec.replace + " " + r.spec.label
        )
        if file_match:
            filename = file_match.group(1)
        else:
            label_clean = "".join(c for c in label if c.isalnum() or c in "._-").strip() or "block"
            filename = f"{label_clean}{export_ext}"
        is_explicit = False

    if filename in written_filenames:
        name_part, ext_part = os.path.splitext(filename)
        if is_explicit:
            counter = 1
            new_filename = f"{name_part}__{counter}{ext_part}"
            while new_filename in written_filenames:
                counter += 1
                new_filename = f"{name_part}__{counter}{ext_part}"
            filename = new_filename
        else:
            label_clean = "".join(c for c in label if c.isalnum() or c in "._-").strip() or "block"
            label_clean = label_clean[:30]
            new_filename = f"{name_part}__{label_clean}{ext_part}"
            if new_filename in written_filenames:
                counter = 1
                fallback = f"{name_part}__{label_clean}__{counter}{ext_part}"
                while fallback in written_filenames:
                    counter += 1
                    fallback = f"{name_part}__{label_clean}__{counter}{ext_part}"
                new_filename = fallback
            filename = new_filename
    written_filenames.add(filename)

    name_part, ext_part = os.path.splitext(filename)
    to_native = cfg.to_native

    if to_native:
        filename_raw = f"{name_part}.raw{ext_part}"
        out_path_raw = os.path.abspath(os.path.join(out_dir, filename_raw))
        out_path_final = os.path.abspath(os.path.join(cfg.native_out_dir or out_dir, filename))
        if not cfg.dry_run:
            os.makedirs(os.path.dirname(out_path_raw) or ".", exist_ok=True)
            os.makedirs(os.path.dirname(out_path_final) or ".", exist_ok=True)
            Path(out_path_raw).write_text(r.removed_content, encoding="utf-8")
    else:
        filename_raw = ""
        out_path_raw = ""
        out_path_final = os.path.abspath(os.path.join(out_dir, filename))
        if not cfg.dry_run:
            os.makedirs(os.path.dirname(out_path_final) or ".", exist_ok=True)
            Path(out_path_final).write_text(r.removed_content, encoding="utf-8")

    matches_deps = []
    if r.removed_content:
        matches = re.findall(
            r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s*(\.|\-\>)\s*([a-zA-Z_][a-zA-Z0-9_]*)\b",
            r.removed_content,
        )
        for obj, op, member in matches:
            matches_deps.append(f"{obj}{op}{member}")
        matches_deps = sorted(set(matches_deps))

    reg_func = to_native or "register_native_module"
    if r.spec.register:
        reg_func = r.spec.register
    elif to_native in ("auto", "auto-native"):
        label_clean = "".join(c for c in label if c.isalnum() or c in "_").strip() or "extracted_block"
        reg_func = f"register_{label_clean.lower()}"

    if profile == "typescript":
        mod_ext = _module_file_ext(cfg.to_module or r.spec.module_kind or None)
        mod_path = r.spec.target_module or os.path.join(
            cfg.module_out_dir or out_dir, f"{name_part}{mod_ext}"
        )
        ws = resolve_consumer_root(cfg.file_path)
        hints = build_typescript_wiring_hints(
            spec=r.spec,
            filename=filename,
            module_out_path=mod_path,
            label=label,
            to_module=cfg.to_module or r.spec.module_kind or None,
            workspace=ws,
        )
    else:
        hints = build_cpp_wiring_hints(
            filename=filename,
            reg_func=reg_func,
            native_out_dir=cfg.native_out_dir,
            out_dir=out_dir,
        )

    export_paths = [out_path_final]
    if out_path_raw:
        export_paths.insert(0, out_path_raw)

    integration = build_integration_hints(
        profile=profile,
        spec=r.spec,
        out_dir=out_dir,
        verify_command=cfg.verify_cmd,
        export_paths=export_paths,
    )

    return {
        "filename": filename,
        "raw_filename": filename_raw,
        "raw_path": out_path_raw,
        "export_path": out_path_final,
        "label": r.spec.label or label,
        "start_line": r.start_line,
        "end_line": r.end_line,
        "line_range": f"{r.start_line}-{r.end_line}",
        "removed_lines_count": r.removed_lines,
        "sha256": hashlib.sha256(r.removed_content.encode("utf-8")).hexdigest(),
        "stub_replacement": r.spec.replace,
        "start_marker": r.spec.start,
        "until_marker": r.spec.until,
        "accessed_external_members": matches_deps,
        "wiring_hints": hints,
        "integration_hints": integration,
    }


def _filter_parent_imports(parent_imports: List[str], results: List[StripResult]) -> List[str]:
    filtered: List[str] = []
    all_extracted = "\n".join(r.removed_content for r in results if r.ok and r.removed_content)
    for imp in parent_imports:
        match = re.search(r'["\'<]([^"\'\s>]+)["\'>]', imp)
        if not match:
            match = re.search(r"\b([a-zA-Z0-9_-]+)\b\s*$", imp)
        if match:
            stem = os.path.splitext(os.path.basename(match.group(1)))[0]
            keywords = re.split(r"[-_]", stem) + [stem]
            matched = any(
                len(kw) > 2 and re.search(re.escape(kw), all_extracted, re.IGNORECASE)
                for kw in keywords
                if kw
            )
            if matched or stem in all_extracted:
                filtered.append(imp)
        else:
            filtered.append(imp)
    return filtered or parent_imports


def _write_report(
    cfg: StripPipelineConfig,
    file_path: str,
    parent_imports: List[str],
    results: List[StripResult],
    exported_meta: List[Dict[str, Any]],
    profile: str,
) -> tuple:
    assert cfg.out_dir
    filtered = _filter_parent_imports(parent_imports, results)
    report_path = os.path.join(cfg.out_dir, "extraction_report.json")
    report_data = {
        "source_file": file_path,
        "profile": profile,
        "parent_imports": filtered,
        "all_parent_imports": parent_imports,
        "extracted_blocks": exported_meta,
        "dangling_references": merge_dangling_reports(exported_meta),
        "integration_hints": {
            "verify_command": cfg.verify_cmd or exported_meta[0].get("integration_hints", {}).get("verify_command", ""),
            "exclude_from_compile": list(
                {
                    p
                    for m in exported_meta
                    for p in m.get("integration_hints", {}).get("exclude_from_compile", [])
                }
            ),
        },
        "conversion_warnings": [],
    }
    Path(report_path).write_text(
        json.dumps(report_data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return report_path, report_data


def _run_native_conversion(cfg, exported_meta, report_path, tc_helper, tc_checkpoint, rollback_fn):
    if not cfg.to_native or cfg.dry_run:
        return None
    conversion_warnings: List[dict] = []
    for meta in exported_meta:
        extracted_path = meta.get("raw_path") or meta.get("export_path")
        out_path_final = meta["export_path"]
        block_to_native = (meta["wiring_hints"].get("register_func") or "").strip()
        if not block_to_native or not extracted_path or not os.path.exists(extracted_path):
            continue
        cmd = [
            sys.executable,
            "-m",
            "apatch.native_converter",
            extracted_path,
            out_path_final,
            block_to_native,
            "--report",
            report_path,
        ]
        child_env = os.environ.copy()
        pkg_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        child_env["PYTHONPATH"] = pkg_parent + os.pathsep + child_env.get("PYTHONPATH", "")
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=True, env=child_env)
            for line in (res.stdout or "").splitlines():
                if line.startswith("CONVERSION_WARNING:"):
                    try:
                        conversion_warnings.append(json.loads(line[19:].strip()))
                    except Exception:
                        pass
        except subprocess.CalledProcessError as err:
            if rollback_fn:
                rollback_fn(tc_helper, tc_checkpoint, cfg.file_path, exported_meta, report_path)
            return StripPipelineResult(ok=False, errors=[err.stderr or "native conversion failed"], exit_code=1)

    blockers = [
        w
        for w in conversion_warnings
        if w.get("kind") in ("needs_evaluator_context", "needs_ast_in_value", "deferred_eval")
    ]
    if blockers:
        blocker_msg = "Static Decoupling Blockers Found: " + ", ".join(
            f"{b.get('callee')} ({b.get('kind')})" for b in blockers
        )
        if cfg.strict and rollback_fn:
            rollback_fn(tc_helper, tc_checkpoint, cfg.file_path, exported_meta, report_path)
            return StripPipelineResult(
                ok=False,
                errors=[blocker_msg, "strict: native conversion blockers"],
                exit_code=1,
            )

    if report_path and os.path.exists(report_path):
        data = json.loads(Path(report_path).read_text(encoding="utf-8"))
        data["conversion_warnings"] = conversion_warnings
        Path(report_path).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return None


def _resolve_module_target(
    target: str,
    workspace: str,
    module_out_dir: Optional[str],
    file_path: str,
) -> str:
    """Resolve hook/component/util output path relative to workspace when needed."""
    if os.path.isabs(target):
        return target
    if module_out_dir:
        return os.path.normpath(os.path.join(module_out_dir, os.path.basename(target)))
    normalized = target.replace("\\", "/")
    if "/" in normalized and not normalized.startswith("./"):
        first_seg = normalized.split("/")[0]
        if os.path.basename(os.path.abspath(workspace)) == first_seg:
            return os.path.normpath(os.path.join(os.path.dirname(workspace), target))
        return os.path.normpath(os.path.join(workspace, target))
    return os.path.normpath(os.path.join(workspace, target))


def _run_module_conversion(
    cfg: StripPipelineConfig,
    exported_meta: List[dict],
    report_data: dict,
    parent_content: str,
) -> None:
    if not cfg.to_module or cfg.dry_run:
        return
    converter = get_converter(cfg.to_module)
    if not converter:
        return
    workspace = resolve_consumer_root(cfg.file_path)
    parent_imports = report_data.get("parent_imports") or report_data.get("all_parent_imports") or []
    ok_specs = [s for s in cfg.specs]
    for meta, r_spec in zip(exported_meta, ok_specs[: len(exported_meta)]):
        raw_path = meta.get("raw_path") or meta.get("export_path")
        if not raw_path or not os.path.exists(raw_path):
            continue
        label = meta.get("label", "extracted")
        mod_ext = _module_file_ext(cfg.to_module)
        if cfg.to_module == "component":
            stem = _pascal_label(label)
        else:
            stem = _camel_module_name(label)
        target = r_spec.target_module or os.path.join(
            cfg.module_out_dir or cfg.out_dir or ".",
            f"{stem}{mod_ext}",
        )
        target = _resolve_module_target(target, workspace, cfg.module_out_dir, cfg.file_path)
        conv_kwargs: Dict[str, Any] = {
            "removed_content": Path(raw_path).read_text(encoding="utf-8"),
            "label": label,
            "parent_imports": parent_imports,
            "out_path": target,
            "parent_content": parent_content,
            "file_path": cfg.file_path,
        }
        if r_spec.replace:
            conv_kwargs["manifest_replace"] = r_spec.replace
        if r_spec.parent_wire:
            conv_kwargs["parent_wire"] = r_spec.parent_wire
        if r_spec.until:
            conv_kwargs["until_marker"] = r_spec.until
        result = converter(**conv_kwargs)
        meta["strip_shape"] = _classify_strip_shape_for_meta(
            conv_kwargs.get("removed_content", "")
        )
        module_path = result.get("module_path")
        if module_path and os.path.isfile(module_path):
            from apatch.imports_resolver import prune_unused_imports

            module_text = Path(module_path).read_text(encoding="utf-8")
            pruned_module = prune_unused_imports(module_text)
            if pruned_module != module_text:
                Path(module_path).write_text(pruned_module, encoding="utf-8")
        meta["module_path"] = module_path
        wh = meta.setdefault("wiring_hints", {})
        export_name = result.get("export_name") or wh.get("export_symbol") or ""
        from apatch.auto_wire import extract_jsx_wire_from_replace

        replace_wire = extract_jsx_wire_from_replace(r_spec.replace, export_name)
        if r_spec.parent_wire:
            wh["parent_wire"] = r_spec.parent_wire
        elif replace_wire:
            wh["parent_wire"] = replace_wire
        else:
            wh["parent_wire"] = result.get("parent_wire", wh.get("parent_wire", ""))
        wh["exports"] = result.get("exports", [])
        if r_spec.parent_import:
            wh["parent_import"] = normalize_ts_import_line(r_spec.parent_import, workspace)
        export_name = result.get("export_name")
        if export_name:
            wh["export_symbol"] = export_name
            if not wh.get("parent_import"):
                wh["import_line"] = workspace_ts_import(target, workspace, export_name)
        ih = meta.setdefault("integration_hints", {})
        ih["target_module"] = target
        ih["module_kind"] = result.get("module_kind", cfg.to_module)
        ih["closure_params"] = result.get("closure_params", [])
        ih["parent_wire"] = wh.get("parent_wire", "")
        if wh.get("parent_import"):
            ih["parent_import"] = wh["parent_import"]
        elif wh.get("import_line"):
            ih["parent_import"] = normalize_ts_import_line(
                wh["import_line"], workspace
            )
        elif r_spec.parent_import:
            ih["parent_import"] = normalize_ts_import_line(
                r_spec.parent_import, workspace
            )


def _classify_strip_shape_for_meta(removed_content: str) -> str:
    return classify_strip_shape(removed_content)


def _camel_module_name(label: str) -> str:
    parts = re.split(r"[_\-\s]+", label)
    if not parts:
        return "extracted"
    return parts[0].lower() + "".join(p.title() for p in parts[1:])


def _emit_wiring_file(cfg, file_path, exported_meta, profile) -> None:
    if not cfg.emit_wiring:
        return
    md = emit_wiring_markdown(profile=profile, source_file=file_path, exported_meta=exported_meta)
    os.makedirs(os.path.dirname(os.path.abspath(cfg.emit_wiring)) or ".", exist_ok=True)
    Path(cfg.emit_wiring).write_text(md, encoding="utf-8")


def _run_post_hooks(cfg, exported_meta, report_path) -> None:
    for meta in exported_meta:
        cmd_str = (cfg.post_hook or "").format(
            extracted_path=meta.get("export_path", ""),
            out_path=meta.get("export_path", ""),
            label=meta.get("label", ""),
            report_path=report_path or "",
        )
        subprocess.run(cmd_str, shell=True, capture_output=True, text=True)


def _verify_tsc_hints(stderr: str) -> List[str]:
    hints: List[str] = []
    for line in (stderr or "").splitlines():
        if "error TS" in line:
            hints.append(line.strip())
    if hints:
        return hints[:10]
    if "verify failed" in (stderr or ""):
        return ["Check verify stdout/stderr; prefer a standalone verify script over inline shell."]
    return []


def _attach_verify_failure_hints(exported_meta: List[Dict[str, Any]], verify_err: str) -> None:
    tsc = _verify_tsc_hints(verify_err)
    for meta in exported_meta:
        ih = meta.setdefault("integration_hints", {})
        ih["verify_failure"] = verify_err[:2000] if verify_err else ""
        if tsc:
            ih["verify_failure_hints"] = tsc
            checklist = list(ih.get("post_strip_checklist") or [])
            checklist.append("Fix TypeScript errors from verify_failure_hints")
            ih["post_strip_checklist"] = checklist


def _run_verify(verify_cmd: str, cwd: str) -> tuple:
    return run_shell_verify(verify_cmd, cwd)


def load_specs_from_manifest_or_markers(
    manifest: Optional[str],
    start_marker: Optional[str],
    until_marker: Optional[str],
    replace_text: str,
    export_filename: Optional[str],
) -> List[StripSpec]:
    if manifest:
        return load_strip_manifest(manifest)
    if start_marker and until_marker:
        return [
            StripSpec(
                start=start_marker,
                until=until_marker,
                replace=replace_text,
                export=export_filename or "",
            )
        ]
    raise ValueError("Provide --manifest or both --start and --until")
