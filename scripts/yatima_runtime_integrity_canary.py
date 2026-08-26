#!/usr/bin/env python3
"""Compatibility entry point for the canonical runtime-integrity canary."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.runtime_integrity_canary import run_canary


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="yatima-integrity-canary-") as temp:
        root = Path(temp)
        report = run_canary(
            output_path=root / "WINDOWS_INTEGRITY_CANARY_RESULTS.json",
            hermes_home=root / "hermes-home",
        )
    print(json.dumps(report, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
