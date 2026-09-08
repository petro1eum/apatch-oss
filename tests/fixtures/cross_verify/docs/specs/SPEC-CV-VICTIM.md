# SPEC-CV-VICTIM — Cross-verify victim fixture

> **apatch artifact:** `spec:SPEC-CV-VICTIM`

## R1 Original token still present

(verify: python3 -c "import pathlib; assert 'ORIGINAL' in pathlib.Path('shared/module.py').read_text()")
