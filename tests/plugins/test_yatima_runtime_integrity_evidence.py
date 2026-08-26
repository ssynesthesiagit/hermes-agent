"""Prevent the plugin evidence surface from drifting from the agent policy."""

from __future__ import annotations

from agent import runtime_integrity as canonical
from plugins.yatima_runtime_integrity import runtime_integrity as adapter


def test_evidence_and_provenance_policy_is_reexported_not_reimplemented():
    assert adapter.ArtifactReadback is canonical.ArtifactReadback
    assert adapter.ProvenanceRecord is canonical.ProvenanceRecord
    assert adapter.ClaimContradiction is canonical.ClaimContradiction
    assert adapter.validate_experiment_receipt is canonical.validate_experiment_receipt
    assert adapter.validate_debate_receipt is canonical.validate_debate_receipt
    assert adapter.readback_artifact is canonical.readback_artifact
    assert adapter.detect_claim_contradiction is canonical.detect_claim_contradiction
