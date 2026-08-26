"""Behavioral subprocess test for the deterministic I1-I8 canary."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from agent.runtime_integrity import PASS_TERMINAL


def test_runtime_integrity_canary_executes_all_cases_and_computes_pass_terminal():
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, str(root / "scripts" / "yatima_runtime_integrity_canary.py")],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["terminal"] == PASS_TERMINAL
    assert report["all_passed"] is True
    assert len(report["cases"]) == 8
    assert list(report["cases"]) == [f"I{index}" for index in range(1, 9)]
    assert all(case["status"] == "PASS" for case in report["cases"].values())
    assert all(case["evidence"] for case in report["cases"].values())
    assert report["activation_performed"] is False
