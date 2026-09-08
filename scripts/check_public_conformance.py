"""Require semantic success; a process exit status alone is not contract evidence."""
import argparse
import json
from pathlib import Path


def accepted(result):
    return (
        isinstance(result, dict)
        and result.get("enabled") is True
        and result.get("ok") is True
        and result.get("contract_holds") is True
        and result.get("gate") == "passed"
        and isinstance(result.get("gated"), int)
        and result["gated"] > 0
        and isinstance(result.get("verified_live"), int)
        and result["verified_live"] > 0
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("result", type=Path)
    args = parser.parse_args()
    try:
        result = json.loads(args.result.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise SystemExit("Missing or invalid conformance result")
    if not accepted(result):
        raise SystemExit("Public conformance qualification failed or did not run")
    print("Live conformance gate passed; inspect advisory buckets separately.")


if __name__ == "__main__":
    main()
