# SPEC-CV-SOURCE — Cross-verify source fixture

> **apatch artifact:** `spec:SPEC-CV-SOURCE`

## R1 Rename shared token

(verify: python3 -c "import pathlib; assert 'RENAMED' in pathlib.Path('shared/module.py').read_text()")
