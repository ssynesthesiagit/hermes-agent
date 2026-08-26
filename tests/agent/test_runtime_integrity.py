from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import yaml


def _evidence_envelope(*, provider: str, model: str, session: str, attempt: str):
    from agent.runtime_integrity import RuntimeEnvelope

    return RuntimeEnvelope.create(
        role_id="brain",
        host="windows-test",
        provider=provider,
        exact_model_id=model,
        model_route=f"https://{provider}.example/v1",
        policy_generation="policy-v1",
        session_id=session,
        attempt_id=attempt,
        capability_tier="C3_RESEARCH_EXECUTION",
        role_ceiling="R4_FRONTIER_JUDGMENT",
        task_ceiling="C3_RESEARCH_EXECUTION",
        evidence_ceiling="C3_RESEARCH_EXECUTION",
        policy_ceiling="R4_FRONTIER_JUDGMENT",
    )


def _trusted_tool_result(ledger, envelope, *, tool_call_id: str):
    ledger.record_tool_receipt(
        envelope=envelope,
        tool_name="terminal",
        tool_call_id=tool_call_id,
        args={"command": "run"},
        result=None,
        status="pre_tool_call",
    )
    return ledger.record_tool_receipt(
        envelope=envelope,
        tool_name="terminal",
        tool_call_id=tool_call_id,
        args={"command": "run"},
        result={"exit_code": 0},
        status="ok",
        bind_to_pre_call=True,
    )


def _trusted_artifact(ledger, tmp_path, name: str, content: str, result_receipt):
    from agent.runtime_integrity import readback_artifact

    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    readback = readback_artifact(path, required=True)
    assert readback.passed is True
    ledger.record_artifact_readback(
        readback,
        result_receipt_ref=result_receipt.receipt_sha256,
        path=path,
    )
    return readback.artifact_sha256


def test_windows_runtime_adapter_attests_effective_runtime_not_model_self_report(
    monkeypatch, tmp_path
):
    home = tmp_path / "hermes-home"
    home.mkdir()
    (home / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "model": {
                    "provider": "openai-codex",
                    "default": "gpt-5.6-sol",
                    "base_url": "https://chatgpt.com/backend-api/codex",
                },
                "runtime_integrity": {
                    "role_id": "brain",
                    "policy_generation": "YATIMA_FLEET_RUNTIME_INTEGRITY_POLICY_V1",
                    "model_capabilities": {"gpt-5.6-sol": "R4_FRONTIER_JUDGMENT"},
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(home))

    client = SimpleNamespace(base_url="https://chatgpt.com/backend-api/codex")
    agent = SimpleNamespace(
        provider="openai-codex",
        requested_provider="openai-codex",
        model="gpt-5.6-sol",
        base_url="https://chatgpt.com/backend-api/codex",
        api_mode="codex_responses",
        client=client,
        model_self_report="google/gemma-3-4b-it",
    )

    from plugins.yatima_runtime_integrity.windows_runtime_identity import WindowsRuntimeIdentityAdapter

    readback = WindowsRuntimeIdentityAdapter().read_agent(agent)

    assert readback.provider == "openai-codex"
    assert readback.exact_model_id == "gpt-5.6-sol"
    assert readback.model_route == "https://chatgpt.com/backend-api/codex"
    assert readback.role_id == "brain"
    assert readback.policy_generation == "YATIMA_FLEET_RUNTIME_INTEGRITY_POLICY_V1"
    assert readback.capability_tier == "R4_FRONTIER_JUDGMENT"
    assert readback.attested is True
    assert readback.sources == (
        "agent_effective_runtime",
        "client_endpoint_readback",
        "profile_config_readback",
    )
    assert "gemma" not in readback.canonical_json().lower()


def test_occupant_change_binds_fresh_envelope_and_attributed_handoff():
    from agent.runtime_integrity import (
        RuntimeEnvelope,
        VerifiedHandoffState,
        detect_occupant_change,
    )

    previous = RuntimeEnvelope.create(
        role_id="brain",
        host="windows-host",
        provider="openai-codex",
        exact_model_id="gpt-5.6-sol",
        model_route="https://chatgpt.com/backend-api/codex",
        policy_generation="policy-v1",
        session_id="session-1",
        attempt_id="attempt-sol",
        capability_tier="R4_FRONTIER_JUDGMENT",
        role_ceiling="R4_FRONTIER_JUDGMENT",
        task_ceiling="C3_RESEARCH_EXECUTION",
        evidence_ceiling="C3_RESEARCH_EXECUTION",
        policy_ceiling="R4_FRONTIER_JUDGMENT",
    )
    current = RuntimeEnvelope.create(
        role_id="brain",
        host="windows-host",
        provider="ollama",
        exact_model_id="gemma3:4b",
        model_route="http://127.0.0.1:11434",
        policy_generation="policy-v1",
        session_id="session-1",
        attempt_id="attempt-gemma",
        capability_tier="C1_ROUTINE_ASSISTANT",
        role_ceiling="R4_FRONTIER_JUDGMENT",
        task_ceiling="C3_RESEARCH_EXECUTION",
        evidence_ceiling="C3_RESEARCH_EXECUTION",
        policy_ceiling="R4_FRONTIER_JUDGMENT",
    )
    assert previous.verify() is True
    for field, value in (
        ("role_id", "other-role"),
        ("host", "other-host"),
        ("provider", "anthropic"),
        ("exact_model_id", "other-model"),
        ("model_route", "https://other.example/v1"),
        ("policy_generation", "policy-v2"),
        ("session_id", "session-2"),
        ("attempt_id", "attempt-2"),
        ("capability_tier", "C1_ROUTINE_ASSISTANT"),
    ):
        assert replace(previous, **{field: value}).verify() is False
    verified = VerifiedHandoffState(
        verified_role_state=("Button 3 evidence remains frozen",),
        owner_decisions=("bounded execution remains disabled",),
        canonical_sources=("sha256:authority",),
        receipt_refs=("sha256:receipt",),
        predecessor_conclusions=("Windows canary passed",),
        pending_tasks=("run integrity canary",),
        unresolved_questions=("owner activation decision",),
    )

    transition = detect_occupant_change(previous, current, verified)

    assert transition.event == "MODEL_OCCUPANT_CHANGE_DETECTED"
    assert transition.fresh_context is True
    assert transition.capability_clamp_active is True
    assert transition.current.effective_authority == "C1_ROUTINE_ASSISTANT"
    assert transition.current.context_id != previous.context_id
    assert transition.handoff_capsule["raw_predecessor_chat_included"] is False
    assert transition.handoff_capsule["predecessor_authorship_transferred"] is False
    assert transition.handoff_capsule["attributed_predecessor_conclusions"] == [
        {
            "statement": "Windows canary passed",
            "origin_envelope_sha256": previous.envelope_sha256,
            "origin_provider": "openai-codex",
            "origin_model": "gpt-5.6-sol",
            "origin_attempt_id": "attempt-sol",
        }
    ]
    assert transition.handoff_capsule["current_capability_tool_envelope"][
        "capability_certificate_id"
    ] == current.capability_certificate_id
    assert "MODEL_OCCUPANT_CHANGE_DETECTED" in transition.owner_notice
    assert "gpt-5.6-sol" in transition.owner_notice
    assert "gemma3:4b" in transition.owner_notice


def test_capability_mismatch_is_deterministic_and_model_confidence_cannot_override():
    from agent.runtime_integrity import admit_capability

    denied = admit_capability(
        required_tier="C3_RESEARCH_EXECUTION",
        role_ceiling="R4_FRONTIER_JUDGMENT",
        task_ceiling="C3_RESEARCH_EXECUTION",
        model_capability="C1_ROUTINE_ASSISTANT",
        evidence_ceiling="C3_RESEARCH_EXECUTION",
        policy_ceiling="R4_FRONTIER_JUDGMENT",
        tool_name="fusion_harness",
        model_claimed_capable=True,
    )

    assert denied.allowed is False
    assert denied.event == "CAPABILITY_MISMATCH"
    assert denied.effective_authority == "C1_ROUTINE_ASSISTANT"
    assert denied.action in {"NARROW", "QUEUE", "ESCALATE", "PAUSE"}
    assert denied.tool_wall_enforced is True
    assert denied.model_self_assessment_considered is False

    clamped_upgrade = admit_capability(
        required_tier="C3_RESEARCH_EXECUTION",
        role_ceiling="C2_BOUNDED_SYNTHESIS",
        task_ceiling="C3_RESEARCH_EXECUTION",
        model_capability="R4_FRONTIER_JUDGMENT",
        evidence_ceiling="C3_RESEARCH_EXECUTION",
        policy_ceiling="R4_FRONTIER_JUDGMENT",
        tool_name="canonical_publish",
    )
    assert clamped_upgrade.allowed is False
    assert clamped_upgrade.effective_authority == "C2_BOUNDED_SYNTHESIS"


def test_provenance_records_preserve_origin_quarantine_and_supersession(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from agent.runtime_integrity import ProvenanceRecord, RuntimeIntegrityLedger

    p0 = ProvenanceRecord.create(
        subject_role="brain",
        origin_model="gemma3:4b",
        origin_provider="ollama",
        origin_attempt_id="attempt-gemma",
        claim_type="experiment_result",
        statement="I ran a debate",
    )
    assert p0.provenance_level == "P0_MODEL_CLAIM"
    assert p0.canonicality == "CANDIDATE"
    assert p0.promote("P3_CANONICAL", controlling_acceptance="owner") is None

    ledger = RuntimeIntegrityLedger()
    actor = _evidence_envelope(
        provider="ollama",
        model="gemma3:4b",
        session="actor-session",
        attempt="actor-attempt",
    )
    verifier = _evidence_envelope(
        provider="openai-codex",
        model="gpt-5.6-sol",
        session="verifier-session",
        attempt="verifier-attempt",
    )
    ledger.record_envelope(actor)
    ledger.record_envelope(verifier)
    tool_result = _trusted_tool_result(ledger, actor, tool_call_id="prov-call")
    p1 = p0.with_tool_evidence((tool_result.receipt_sha256,), ledger=ledger)
    review = ledger.record_independent_verification(
        subject_refs=p1.evidence_refs,
        verifier_envelope=verifier,
        accepted=True,
    )
    p2 = p1.promote(
        "P2_INDEPENDENTLY_VERIFIED",
        independent_receipt=review,
        ledger=ledger,
    )
    assert p2 is not None
    acceptance = ledger.record_controlling_acceptance(
        subject_ref=review, authority="owner", accepted=True
    )
    p3 = p2.promote(
        "P3_CANONICAL", controlling_acceptance=acceptance, ledger=ledger
    )
    assert p3 is not None
    assert p3.provenance_level == "P3_CANONICAL"
    assert p3.canonicality == "CANONICAL"

    quarantined = p3.quarantine("CLAIM_EVIDENCE_CONTRADICTION")
    assert quarantined.quarantine_state == "QUARANTINED"
    assert quarantined.canonicality == "PROMOTION_BLOCKED"
    assert quarantined.promote("P3_CANONICAL", controlling_acceptance="owner") is None

    corrected = ProvenanceRecord.create(
        subject_role="brain",
        origin_model="gpt-5.6-sol",
        origin_provider="openai-codex",
        origin_attempt_id="attempt-sol",
        claim_type="experiment_result",
        statement="No external debate occurred",
        evidence_refs=("sha256:telemetry",),
        provenance_level="P2_INDEPENDENTLY_VERIFIED",
    ).supersede(quarantined)
    assert corrected.supersedes_record_id == quarantined.record_id
    assert corrected.origin_attempt_id == "attempt-sol"


def test_artifact_readback_detects_missing_or_invalid_output(tmp_path):
    from agent.runtime_integrity import readback_artifact

    missing = readback_artifact(
        tmp_path / "missing.json", required=True, required_json_keys=("status",)
    )
    assert missing.passed is False
    assert missing.event == "SILENT_FAILURE_DETECTED"
    assert missing.material_status == "NOT_PASS"

    invalid_path = tmp_path / "invalid.json"
    invalid_path.write_text("not-json", encoding="utf-8")
    invalid = readback_artifact(
        invalid_path, required=True, required_json_keys=("status",)
    )
    assert invalid.passed is False
    assert invalid.event == "SILENT_FAILURE_DETECTED"

    valid_path = tmp_path / "valid.json"
    valid_path.write_text('{"status":"PASS"}\n', encoding="utf-8")
    valid = readback_artifact(
        valid_path, required=True, required_json_keys=("status",)
    )
    assert valid.passed is True
    assert valid.event == "ARTIFACT_READBACK_VERIFIED"
    assert valid.artifact_sha256
    assert valid.readback_receipt_sha256


def test_single_model_personas_cannot_be_published_as_multi_model_debate(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from agent.runtime_integrity import RuntimeIntegrityLedger, validate_debate_receipt

    fabricated = validate_debate_receipt(
        {
            "claimed_participants": ["Opus", "Sol", "Ox Alpha"],
            "current_runtime_model": "gemma3:4b",
            "external_participant_call_count": 0,
            "personas": ["Opus", "Sol", "Ox Alpha"],
        }
    )
    assert fabricated.classification == "SIMULATION"
    assert fabricated.event == "NOT_MULTI_MODEL_DEBATE"
    assert fabricated.experimental_multi_model_evidence is False
    assert fabricated.publication_allowed is False
    assert "PUBLICATION_DENIED_UNVERIFIED_EVIDENCE" in fabricated.terminals

    ledger = RuntimeIntegrityLedger()
    participant_one = _evidence_envelope(
        provider="anthropic",
        model="opus",
        session="s1",
        attempt="participant-1",
    )
    participant_two = _evidence_envelope(
        provider="openai-codex",
        model="sol",
        session="s2",
        attempt="participant-2",
    )
    ledger.record_envelope(participant_one)
    ledger.record_envelope(participant_two)
    harness = _trusted_tool_result(
        ledger, participant_one, tool_call_id="fusion-harness-call"
    )
    stack_digest = _trusted_artifact(
        ledger, tmp_path, "stack.json", '{"stack":"real"}', harness
    )
    transcript_digest = _trusted_artifact(
        ledger, tmp_path, "transcript.json", '{"round":1}', harness
    )
    final_digest = _trusted_artifact(
        ledger, tmp_path, "final.json", '{"status":"PASS"}', harness
    )
    independent = ledger.record_independent_verification(
        subject_refs=(harness.receipt_sha256,),
        verifier_envelope=participant_two,
        accepted=True,
    )
    verified = validate_debate_receipt(
        {
            "harness_run_id": "fusion-run-1",
            "harness_tool_receipt_sha256": harness.receipt_sha256,
            "stack_manifest_sha256": stack_digest,
            "participants": [
                {
                    "provider": "anthropic",
                    "model": "opus",
                    "session_id": "s1",
                    "runtime_envelope_sha256": participant_one.envelope_sha256,
                },
                {
                    "provider": "openai-codex",
                    "model": "sol",
                    "session_id": "s2",
                    "runtime_envelope_sha256": participant_two.envelope_sha256,
                },
            ],
            "external_participant_call_count": 2,
            "round_transcript_sha256": [transcript_digest],
            "aggregator": {
                "provider": "openai-codex",
                "model": "sol",
                "session_id": "s2",
                "runtime_envelope_sha256": participant_two.envelope_sha256,
            },
            "final_output_sha256": final_digest,
            "independent_validation_receipt": independent,
        },
        ledger=ledger,
    )
    assert verified.classification == "MULTI_MODEL_DEBATE"
    assert verified.event == "DEBATE_RECEIPT_VERIFIED"
    assert verified.publication_allowed is True


def test_fake_completed_action_without_receipt_is_not_proven_or_publishable(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from agent.runtime_integrity import RuntimeIntegrityLedger, validate_experiment_receipt

    fake = validate_experiment_receipt(
        {"model_claim": "experiment succeeded", "exit_status": 0}
    )
    assert fake.action_proven is False
    assert fake.publication_allowed is False
    assert fake.event == "ACTION_NOT_PROVEN"
    assert "NO_EXECUTION_RECEIPT" in fake.terminals
    assert "PUBLICATION_DENIED_UNVERIFIED_EVIDENCE" in fake.terminals

    ledger = RuntimeIntegrityLedger()
    actor = _evidence_envelope(
        provider="openai-codex",
        model="gpt-5.6-sol",
        session="experiment-session",
        attempt="experiment-attempt",
    )
    verifier = _evidence_envelope(
        provider="anthropic",
        model="opus",
        session="experiment-review-session",
        attempt="experiment-review-attempt",
    )
    ledger.record_envelope(actor)
    ledger.record_envelope(verifier)
    execution = _trusted_tool_result(
        ledger, actor, tool_call_id="experiment-call"
    )
    input_digest = _trusted_artifact(
        ledger, tmp_path, "input.json", '{"input":1}', execution
    )
    output_digest = _trusted_artifact(
        ledger, tmp_path, "result.json", '{"status":"PASS"}', execution
    )
    ledger.record_source_revision(
        source_revision="a" * 40,
        tool_receipt_ref=execution.receipt_sha256,
    )
    review = ledger.record_independent_verification(
        subject_refs=(execution.receipt_sha256,),
        verifier_envelope=verifier,
        accepted=True,
    )
    verified = validate_experiment_receipt(
        {
            "run_id": "run-1",
            "invocation": ["python", "canary.py"],
            "started_at": "2026-08-26T12:00:00Z",
            "ended_at": "2026-08-26T12:00:01Z",
            "exit_status": 0,
            "source_revision": "a" * 40,
            "input_sha256": {"input.json": input_digest},
            "output_sha256": {"result.json": output_digest},
            "runtime_envelope_sha256": actor.envelope_sha256,
            "tool_receipt_sha256": execution.receipt_sha256,
            "independent_review_receipt": review,
        },
        ledger=ledger,
    )
    assert verified.action_proven is True
    assert verified.publication_allowed is True
    assert verified.event == "EXPERIMENT_RECEIPT_VERIFIED"


def test_claim_evidence_contradiction_quarantines_and_blocks_promotion():
    from agent.runtime_integrity import ProvenanceRecord, detect_claim_contradiction

    candidate = ProvenanceRecord.create(
        subject_role="brain",
        origin_model="gemma3:4b",
        origin_provider="ollama",
        origin_attempt_id="attempt-gemma",
        claim_type="multi_model_debate",
        statement="I ran Opus, Sol, and Ox Alpha through Fusion Harness",
    )
    contradiction = detect_claim_contradiction(
        candidate,
        claimed_minimums={
            "fusion_invocations": 1,
            "external_participant_model_calls": 3,
        },
        telemetry={
            "fusion_invocations": 0,
            "external_participant_model_calls": 0,
            "current_runtime_model": "gemma3:4b",
        },
    )

    assert contradiction.event == "CLAIM_EVIDENCE_CONTRADICTION"
    assert contradiction.contradicted is True
    assert contradiction.quarantined_record.quarantine_state == "QUARANTINED"
    assert contradiction.promotion_allowed is False
    assert contradiction.raw_evidence_preserved is True
    assert contradiction.attempt_state == "SUSPECT"


def test_runtime_ledger_reuses_verification_evidence_db_and_binds_receipts(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from agent.runtime_integrity import (
        RuntimeEnvelope,
        RuntimeIntegrityLedger,
        create_tool_receipt,
    )

    envelope = RuntimeEnvelope.create(
        role_id="brain",
        host="windows-host",
        provider="openai-codex",
        exact_model_id="gpt-5.6-sol",
        model_route="https://chatgpt.com/backend-api/codex",
        policy_generation="policy-v1",
        session_id="session-ledger",
        attempt_id="attempt-ledger",
        capability_tier="R4_FRONTIER_JUDGMENT",
        role_ceiling="R4_FRONTIER_JUDGMENT",
        task_ceiling="C3_RESEARCH_EXECUTION",
        evidence_ceiling="C3_RESEARCH_EXECUTION",
        policy_ceiling="R4_FRONTIER_JUDGMENT",
    )
    ledger = RuntimeIntegrityLedger()
    ledger.record_envelope(envelope)
    receipt = ledger.record_tool_receipt(
        envelope=envelope,
        tool_name="terminal",
        tool_call_id="call-1",
        args={"command": "python canary.py"},
        result={"exit_code": 0, "output": "PASS"},
        status="ok",
    )

    restored = ledger.current_occupant("brain")
    assert restored == envelope
    assert receipt.runtime_envelope_sha256 == envelope.envelope_sha256
    assert receipt.receipt_sha256
    assert receipt.args_sha256
    assert receipt.result_sha256
    same_receipt = create_tool_receipt(
        envelope=envelope,
        tool_name="terminal",
        tool_call_id="call-1",
        args={"command": "python canary.py"},
        result={"exit_code": 0, "output": "PASS"},
        status="ok",
    )
    assert same_receipt == receipt
    assert create_tool_receipt(
        envelope=envelope,
        tool_name="terminal",
        tool_call_id="call-1",
        args={"command": "python different.py"},
        result={"exit_code": 0, "output": "PASS"},
        status="ok",
    ).receipt_sha256 != receipt.receipt_sha256
    assert create_tool_receipt(
        envelope=envelope,
        tool_name="terminal",
        tool_call_id="call-1",
        args={"command": "python canary.py"},
        result={"exit_code": 1, "output": "FAIL"},
        status="error",
    ).receipt_sha256 != receipt.receipt_sha256
    assert (tmp_path / "verification_evidence.db").is_file()
    assert sorted(path.name for path in tmp_path.glob("*.db")) == [
        "verification_evidence.db"
    ]


def test_enabled_turn_integration_filters_predecessor_history_and_enforces_tool_wall(
    monkeypatch, tmp_path
):
    import yaml
    from types import SimpleNamespace

    home = tmp_path / "home"
    home.mkdir()
    config_path = home / "config.yaml"

    def write_config(provider, model, base_url, tier):
        config_path.write_text(
            yaml.safe_dump(
                {
                    "model": {
                        "provider": provider,
                        "default": model,
                        "base_url": base_url,
                    },
                    "runtime_integrity": {
                        "enabled": True,
                        "role_id": "brain",
                        "policy_generation": "policy-v1",
                        "role_ceiling": "R4_FRONTIER_JUDGMENT",
                        "task_ceiling": "C3_RESEARCH_EXECUTION",
                        "evidence_ceiling": "C3_RESEARCH_EXECUTION",
                        "policy_ceiling": "R4_FRONTIER_JUDGMENT",
                        "model_capabilities": {model: tier},
                        "tool_requirements": {
                            "fusion_harness": "C3_RESEARCH_EXECUTION"
                        },
                    },
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )

    monkeypatch.setenv("HERMES_HOME", str(home))
    notices = []
    write_config(
        "openai-codex",
        "gpt-5.6-sol",
        "https://chatgpt.com/backend-api/codex",
        "R4_FRONTIER_JUDGMENT",
    )
    agent = SimpleNamespace(
        provider="openai-codex",
        requested_provider="openai-codex",
        model="gpt-5.6-sol",
        base_url="https://chatgpt.com/backend-api/codex",
        api_mode="codex_responses",
        client=SimpleNamespace(base_url="https://chatgpt.com/backend-api/codex"),
        session_id="session-integrated",
        _emit_status=notices.append,
    )
    from agent.runtime_integrity import (
        observe_lifecycle,
        prepare_agent_turn,
        pre_tool_call_directive,
    )

    initial = prepare_agent_turn(
        agent,
        conversation_history=[{"role": "user", "content": "old"}],
        attempt_id="attempt-sol",
    )
    assert initial.occupant_changed is False
    assert initial.conversation_history == [{"role": "user", "content": "old"}]

    write_config(
        "ollama",
        "gemma3:4b",
        "http://127.0.0.1:11434",
        "C1_ROUTINE_ASSISTANT",
    )
    agent.provider = "ollama"
    agent.requested_provider = "ollama"
    agent.model = "gemma3:4b"
    agent.base_url = "http://127.0.0.1:11434"
    agent.api_mode = "chat_completions"
    agent.client = SimpleNamespace(base_url="http://127.0.0.1:11434")

    changed = prepare_agent_turn(
        agent,
        conversation_history=[
            {"role": "user", "content": "I ran the experiment"},
            {"role": "assistant", "content": "I proved it"},
        ],
        attempt_id="attempt-gemma",
    )
    assert changed.occupant_changed is True
    assert changed.conversation_history is None
    assert "MODEL_OCCUPANT_CHANGE_DETECTED" in changed.injected_context
    assert "raw_predecessor_chat_included" in changed.injected_context
    assert notices and "capability_clamp_active=true" in notices[-1]

    directive = pre_tool_call_directive(
        tool_name="fusion_harness",
        session_id="session-integrated",
        tool_call_id="call-wall",
    )
    assert directive == {
        "action": "block",
        "message": (
            "CAPABILITY_MISMATCH tool=fusion_harness "
            "required=C3_RESEARCH_EXECUTION effective=C1_ROUTINE_ASSISTANT "
            "action=ESCALATE"
        ),
    }

    observe_lifecycle(
        "post_tool_call",
        tool_name="read_file",
        args={"path": "public-safe.txt"},
        result={"success": True},
        session_id="session-integrated",
        tool_call_id="call-read",
        status="ok",
    )


def test_midturn_fallback_replaces_raw_history_with_handoff(monkeypatch, tmp_path):
    import yaml
    from types import SimpleNamespace

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))

    def configure(provider, model, route, tier):
        (home / "config.yaml").write_text(
            yaml.safe_dump(
                {
                    "model": {
                        "provider": provider,
                        "default": model,
                        "base_url": route,
                    },
                    "runtime_integrity": {
                        "enabled": True,
                        "role_id": "brain",
                        "policy_generation": "policy-v1",
                        "role_ceiling": "R4_FRONTIER_JUDGMENT",
                        "task_ceiling": "C3_RESEARCH_EXECUTION",
                        "evidence_ceiling": "C3_RESEARCH_EXECUTION",
                        "policy_ceiling": "R4_FRONTIER_JUDGMENT",
                        "model_capabilities": {model: tier},
                    },
                }
            ),
            encoding="utf-8",
        )

    notices = []
    configure(
        "openai-codex",
        "gpt-5.6-sol",
        "https://chatgpt.com/backend-api/codex",
        "R4_FRONTIER_JUDGMENT",
    )
    agent = SimpleNamespace(
        provider="openai-codex",
        model="gpt-5.6-sol",
        base_url="https://chatgpt.com/backend-api/codex",
        api_mode="codex_responses",
        client=SimpleNamespace(base_url="https://chatgpt.com/backend-api/codex"),
        session_id="session-midturn",
        _emit_status=notices.append,
    )
    from agent.runtime_integrity import (
        RuntimeIntegrityViolation,
        apply_midturn_occupant_boundary,
        enforce_pre_api_runtime,
        prepare_agent_turn,
    )

    prepare_agent_turn(agent, conversation_history=[], attempt_id="api-primary")
    configure(
        "ollama",
        "gemma3:4b",
        "http://127.0.0.1:11434",
        "C1_ROUTINE_ASSISTANT",
    )
    agent.provider = "ollama"
    agent.model = "gemma3:4b"
    agent.base_url = "http://127.0.0.1:11434"
    agent.api_mode = "chat_completions"
    agent.client = SimpleNamespace(base_url="http://127.0.0.1:11434")
    api_messages = [
        {"role": "system", "content": "stable"},
        {"role": "user", "content": "old predecessor request"},
        {"role": "assistant", "content": "old predecessor claim"},
        {"role": "user", "content": "current request"},
    ]

    transition = apply_midturn_occupant_boundary(
        agent, api_messages, attempt_id="api-fallback"
    )
    assert transition is not None
    assert [message["role"] for message in api_messages] == ["system", "user"]
    assert "old predecessor" not in str(api_messages)
    assert "current request" in api_messages[-1]["content"]
    assert "MODEL_OCCUPANT_CHANGE_DETECTED" in api_messages[-1]["content"]
    enforce_pre_api_runtime(agent, api_request_id="api-fallback")

    agent.model = "unattested-model"
    try:
        enforce_pre_api_runtime(agent, api_request_id="api-drift")
    except RuntimeIntegrityViolation as exc:
        assert "RUNTIME_IDENTITY_ATTESTATION_FAILED" in str(exc)
    else:
        raise AssertionError("unexpected runtime drift did not fail closed")


def test_deterministic_canary_emits_concrete_i1_i8_evidence(monkeypatch, tmp_path):
    from scripts.runtime_integrity_canary import run_canary

    home = tmp_path / "canary-home"
    output = tmp_path / "WINDOWS_INTEGRITY_CANARY_RESULTS.json"
    monkeypatch.setenv("HERMES_HOME", str(home))

    result = run_canary(output_path=output, hermes_home=home)

    assert list(result["cases"]) == [f"I{index}" for index in range(1, 9)]
    assert all(case["status"] == "PASS" for case in result["cases"].values())
    assert all(case["evidence"] for case in result["cases"].values())
    assert all(case["evidence_sha256"] for case in result["cases"].values())
    assert result["all_passed"] is True
    assert result["terminal"] == (
        "YATIMA_FLEET_V4_RUNTIME_INTEGRITY_CANARY_PASSED_"
        "ACTIVATION_STILL_OWNER_GATED"
    )
    assert result["activation_performed"] is False
    assert output.is_file()
    assert (home / "verification_evidence.db").is_file()
