"""Packaging and re-export contracts for the opt-in adapter."""

from __future__ import annotations

import hashlib
from pathlib import Path

from agent import runtime_integrity as canonical
from plugins.yatima_runtime_integrity import runtime_integrity as adapter


def test_policy_domain_is_reexported_from_the_integrated_agent_module():
    assert adapter.CapabilityTier is canonical.CapabilityTier
    assert adapter.CapabilityAdmission is canonical.CapabilityAdmission
    assert adapter.RuntimeEnvelope is canonical.RuntimeEnvelope
    assert adapter.ToolReceipt is canonical.ToolReceipt
    assert adapter.ProvenanceRecord is canonical.ProvenanceRecord
    assert adapter.admit_capability is canonical.admit_capability
    assert adapter.create_tool_receipt is canonical.create_tool_receipt
    assert adapter.detect_occupant_change is canonical.detect_occupant_change
    assert adapter.validate_policy is canonical.validate_policy
    assert adapter.POLICY_GENERATION == canonical.POLICY_GENERATION
    assert adapter.PASS_TERMINAL == canonical.PASS_TERMINAL
    assert adapter.FAIL_TERMINAL == canonical.FAIL_TERMINAL


def test_bundled_policy_remains_validated_owner_gated_and_packaged():
    policy = adapter.load_policy()
    assert policy["schema_version"] == 1
    assert policy["status"] == "OWNER_DIRECTED_PRE_ACTIVATION_POLICY"
    assert policy["architecture_generation"] == "V4"
    assert policy["execution_enabled"] is False
    assert policy["bounded_activation_allowed"] is False
    assert policy["activation"]["automatic_activation"] is False
    assert policy["activation"]["button3_final_reconciliation_required"] is True
    assert policy["activation"]["runtime_integrity_canary_required"] is True
    assert tuple(policy["capability_model"]["tiers"]) == tuple(
        tier.name for tier in canonical.CapabilityTier
    )
    assert (
        policy["pre_activation_integrity_canary"]["pass_terminal"]
        == canonical.PASS_TERMINAL
    )
    assert (
        policy["pre_activation_integrity_canary"]["fail_terminal"]
        == canonical.FAIL_TERMINAL
    )
    assert adapter.validate_policy(policy) == policy

    package_dir = Path(adapter.__file__).parent
    policy_path = package_dir / adapter.POLICY_FILENAME
    assert (package_dir / "__init__.py").is_file()
    assert (package_dir / "plugin.yaml").is_file()
    assert adapter.POLICY_FILENAME == "YATIMA_FLEET_RUNTIME_INTEGRITY_POLICY_V1.yaml"
    assert adapter.AUTHORITATIVE_YATIMA_COMMIT == (
        "8d78fa67d0aaf57e7f2d7d763ee15cb98d47369f"
    )
    assert adapter.AUTHORITATIVE_POLICY_SHA256 == (
        "1a1889d291cf1fb836dca935c518dfdc18c0f549344ed9fb672b8e769d188f10"
    )
    assert policy_path.is_file()
    assert hashlib.sha256(policy_path.read_bytes()).hexdigest() == (
        adapter.AUTHORITATIVE_POLICY_SHA256
    )
