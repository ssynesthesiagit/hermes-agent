from __future__ import annotations

from types import SimpleNamespace

from agent.runtime_integrity import (
    ProvenanceRecord,
    RuntimeEnvelope,
    RuntimeIntegrityLedger,
    readback_artifact,
    validate_experiment_receipt,
)
from plugins.yatima_runtime_integrity import runtime_integrity as plugin


def _config(**overrides):
    config = {
        "enabled": True,
        "role_id": "brain",
        "policy_generation": "policy-v1",
        "role_ceiling": "R4_FRONTIER_JUDGMENT",
        "task_ceiling": "R4_FRONTIER_JUDGMENT",
        "evidence_ceiling": "R4_FRONTIER_JUDGMENT",
        "policy_ceiling": "R4_FRONTIER_JUDGMENT",
        "capabilities": {
            "gpt-5.6-sol": "R4_FRONTIER_JUDGMENT",
            "gemma3:4b": "C1_ROUTINE_ASSISTANT",
        },
        "tool_requirements": {"write_file": "C2_BOUNDED_SYNTHESIS"},
        "evidence_requirements": {
            "memory": "memory",
            "canonical_publish": "publication",
            "experiment_runner": "experiment",
            "fusion_harness": "debate",
        },
    }
    config.update(overrides)
    return config


def _agent(*, provider, model, route, session_id):
    return SimpleNamespace(
        provider=provider,
        requested_provider=provider,
        model=model,
        model_route=route,
        base_url=route,
        api_mode=("codex_responses" if provider == "openai-codex" else "chat_completions"),
        session_id=session_id,
        client=SimpleNamespace(base_url=route),
    )


def _request(adapter, agent, request, attempt, *, metadata=None, notices=None):
    metadata = metadata or {}
    return adapter.llm_request_callback(
        request=request,
        session_id=agent.session_id,
        api_request_id=attempt,
        runtime_agent=agent,
        runtime_notice_callback=(notices.append if notices is not None else None),
        model=metadata.get("model", agent.model),
        provider=metadata.get("provider", agent.provider),
        base_url=metadata.get("base_url", agent.base_url),
        api_mode=metadata.get("api_mode", agent.api_mode),
    ) or {
        "request": request,
        "changed": False,
        "blocked": False,
    }


def _envelope(*, provider, model, session_id, attempt, tier="C3_RESEARCH_EXECUTION"):
    return RuntimeEnvelope.create(
        role_id="brain",
        host="windows-host",
        provider=provider,
        exact_model_id=model,
        model_route=f"https://{provider}.example/v1",
        policy_generation="policy-v1",
        session_id=session_id,
        attempt_id=attempt,
        capability_tier=tier,
        role_ceiling="R4_FRONTIER_JUDGMENT",
        task_ceiling="R4_FRONTIER_JUDGMENT",
        evidence_ceiling="R4_FRONTIER_JUDGMENT",
        policy_ceiling="R4_FRONTIER_JUDGMENT",
    )


class _RegistrationContext:
    def __init__(self, settings):
        self.settings = settings
        self.middleware = []
        self.hooks = []

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def register_middleware(self, kind, callback):
        self.middleware.append((kind, callback))

    def register_hook(self, name, callback):
        self.hooks.append((name, callback))


def test_terra_1_middleware_metadata_cannot_self_attest_runtime(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    adapter = plugin.RuntimeIntegrityAdapter(config=_config())
    forged = adapter.llm_request_callback(
        request={"messages": [{"role": "user", "content": "run"}]},
        session_id="forged-session",
        api_request_id="forged-api",
        model="arbitrary-model",
        provider="arbitrary-provider",
        base_url="https://attacker.invalid/v1",
        api_mode="chat_completions",
    )
    assert forged is not None
    assert forged["blocked"] is True
    assert forged["event"] == "RUNTIME_IDENTITY_ATTESTATION_FAILED"

    live = _agent(
        provider="openai-codex",
        model="gpt-5.6-sol",
        route="https://chatgpt.com/backend-api/codex",
        session_id="trusted-session",
    )
    trusted = _request(
        adapter,
        live,
        {"messages": [{"role": "user", "content": "run"}]},
        "trusted-api",
        metadata={
            "model": "forged-model",
            "provider": "forged-provider",
            "base_url": "https://attacker.invalid/v1",
        },
    )
    assert trusted["blocked"] is False
    assert trusted["envelope"]["provider"] == "openai-codex"
    assert trusted["envelope"]["exact_model_id"] == "gpt-5.6-sol"


def test_terra_2_plugin_state_is_canonical_and_durable(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    agent = _agent(
        provider="openai-codex",
        model="gpt-5.6-sol",
        route="https://chatgpt.com/backend-api/codex",
        session_id="durable-session",
    )
    first = plugin.RuntimeIntegrityAdapter(config=_config())
    envelope = first.bind_agent(agent, attempt_id="api-1")
    pre = first.pre_tool_call(
        tool_name="write_file",
        args={"path": "out.txt"},
        session_id=agent.session_id,
        tool_call_id="call-1",
        api_request_id="api-1",
    )
    post = first.post_tool_call(
        tool_name="write_file",
        args={"path": "out.txt"},
        result={"ok": True},
        session_id=agent.session_id,
        tool_call_id="call-1",
        api_request_id="api-1",
        status="ok",
    )
    first.on_session_end(session_id=agent.session_id)

    restarted = plugin.RuntimeIntegrityAdapter(config=_config())
    assert RuntimeIntegrityLedger().current_occupant("brain") == envelope
    assert restarted.current_envelope(agent.session_id) == envelope
    assert [item.receipt_sha256 for item in restarted.receipts_for_session(agent.session_id)] == [
        pre["receipt"]["receipt_sha256"],
        post["receipt"]["receipt_sha256"],
    ]


def test_terra_3_fresh_context_persists_on_followups_and_clears_provider_threads(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    adapter = plugin.RuntimeIntegrityAdapter(config=_config())
    primary = _agent(
        provider="openai-codex",
        model="gpt-5.6-sol",
        route="https://chatgpt.com/backend-api/codex",
        session_id="swap-session",
    )
    fallback = _agent(
        provider="ollama",
        model="gemma3:4b",
        route="http://127.0.0.1:11434",
        session_id="swap-session",
    )
    history = [
        {"role": "system", "content": "stable"},
        {"role": "user", "content": "predecessor ask"},
        {"role": "assistant", "content": "predecessor answer"},
        {"role": "user", "content": "current ask"},
    ]
    _request(adapter, primary, {"messages": history}, "api-primary")
    swapped = _request(
        adapter,
        fallback,
        {
            "messages": history,
            "previous_response_id": "resp-predecessor",
            "conversation_id": "conv-predecessor",
            "thread_id": "thread-predecessor",
        },
        "api-swap",
    )
    followup = _request(
        adapter,
        fallback,
        {
            "messages": [
                *history,
                {"role": "assistant", "content": "new occupant tool call"},
                {"role": "tool", "content": "new occupant result"},
            ],
            "previous_response_id": "resp-predecessor",
        },
        "api-followup",
    )

    for result in (swapped, followup):
        assert "predecessor ask" not in str(result["request"])
        assert "predecessor answer" not in str(result["request"])
        assert "previous_response_id" not in result["request"]
        assert "conversation_id" not in result["request"]
        assert "thread_id" not in result["request"]
    assert "new occupant tool call" in str(followup["request"])
    assert "new occupant result" in str(followup["request"])


def test_terra_4_cross_session_restart_records_complete_durable_transition(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    primary = _agent(
        provider="openai-codex",
        model="gpt-5.6-sol",
        route="https://chatgpt.com/backend-api/codex",
        session_id="session-before-restart",
    )
    fallback = _agent(
        provider="ollama",
        model="gemma3:4b",
        route="http://127.0.0.1:11434",
        session_id="session-after-restart",
    )
    predecessor = [
        {"role": "user", "content": "predecessor private segment"},
        {"role": "assistant", "content": "predecessor conclusion"},
        {"role": "user", "content": "new request"},
    ]
    _request(
        plugin.RuntimeIntegrityAdapter(config=_config()),
        primary,
        {"messages": predecessor},
        "api-before",
    )
    notices = []
    changed = _request(
        plugin.RuntimeIntegrityAdapter(config=_config()),
        fallback,
        {"messages": predecessor},
        "api-after",
        notices=notices,
    )
    assert changed["changed"] is True
    assert notices and "MODEL_OCCUPANT_CHANGE_DETECTED" in notices[-1]

    durable = RuntimeIntegrityLedger().latest_transition("brain")
    assert durable is not None
    assert durable["previous_session_id"] == "session-before-restart"
    assert durable["current_session_id"] == "session-after-restart"
    assert durable["predecessor_segment_sha256"]
    assert durable["handoff_capsule"]["raw_predecessor_chat_included"] is False
    assert durable["new_context"]["provider"] == "ollama"
    assert "attributed_predecessor_conclusions" in durable["attribution"]
    assert durable["owner_notice"] == notices[-1]


def test_terra_5_registered_protected_seams_fail_closed_and_bind_results(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    ctx = _RegistrationContext(_config())
    adapter = plugin.register_plugin(ctx)
    assert adapter is not None
    callbacks = {name: callback for name, callback in ctx.hooks}
    assert "on_session_finalize" in callbacks

    agent = _agent(
        provider="openai-codex",
        model="gpt-5.6-sol",
        route="https://chatgpt.com/backend-api/codex",
        session_id="seams-session",
    )
    _request(
        adapter,
        agent,
        {"messages": [{"role": "user", "content": "run"}]},
        "api-seams",
    )
    for tool_name in ("memory", "canonical_publish", "experiment_runner", "fusion_harness"):
        denied = callbacks["pre_tool_call"](
            tool_name=tool_name,
            args={"action": "add", "content": "consequential claim"},
            session_id=agent.session_id,
            tool_call_id=f"call-{tool_name}",
            api_request_id="api-seams",
        )
        assert denied["action"] == "block"
        assert denied["event"].endswith("_ADMISSION_DENIED")

    pre = callbacks["pre_tool_call"](
        tool_name="read_file",
        args={"path": "safe.txt"},
        session_id=agent.session_id,
        tool_call_id="call-bound",
        api_request_id="api-seams",
    )
    post = callbacks["post_tool_call"](
        tool_name="read_file",
        args={"path": "safe.txt"},
        result={"content": "safe"},
        session_id=agent.session_id,
        tool_call_id="call-bound",
        api_request_id="api-seams",
        status="ok",
    )
    orphan = callbacks["post_tool_call"](
        tool_name="read_file",
        args={"path": "orphan.txt"},
        result={"content": "unbound"},
        session_id=agent.session_id,
        tool_call_id="call-orphan",
        api_request_id="api-seams",
        status="ok",
    )
    assert post["receipt"]["previous_receipt_sha256"] == pre["receipt"]["receipt_sha256"]
    assert orphan["allowed"] is False
    assert orphan["event"] == "ACTION_NOT_PROVEN"


def test_terra_6_provenance_promotions_resolve_real_ledger_records(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    ledger = RuntimeIntegrityLedger()
    p0 = ProvenanceRecord.create(
        subject_role="brain",
        origin_model="gpt-5.6-sol",
        origin_provider="openai-codex",
        origin_attempt_id="attempt-1",
        claim_type="experiment_result",
        statement="experiment passed",
    )
    fake = p0.with_tool_evidence(("a" * 64,))
    assert fake.provenance_level == "P0_MODEL_CLAIM"

    actor = _envelope(
        provider="openai-codex",
        model="gpt-5.6-sol",
        session_id="actor-session",
        attempt="actor-attempt",
    )
    verifier = _envelope(
        provider="anthropic",
        model="claude-opus",
        session_id="verifier-session",
        attempt="verifier-attempt",
    )
    ledger.record_envelope(actor)
    ledger.record_envelope(verifier)
    ledger.record_tool_receipt(
        envelope=actor,
        tool_name="terminal",
        tool_call_id="tool-1",
        args={"command": "run"},
        result=None,
        status="pre_tool_call",
    )
    result_receipt = ledger.record_tool_receipt(
        envelope=actor,
        tool_name="terminal",
        tool_call_id="tool-1",
        args={"command": "run"},
        result={"exit_code": 0},
        status="ok",
        bind_to_pre_call=True,
    )
    p1 = p0.with_tool_evidence((result_receipt.receipt_sha256,), ledger=ledger)
    independent = ledger.record_independent_verification(
        subject_refs=p1.evidence_refs,
        verifier_envelope=verifier,
        accepted=True,
    )
    p2 = p1.promote(
        "P2_INDEPENDENTLY_VERIFIED",
        independent_receipt=independent,
        ledger=ledger,
    )
    assert p2 is not None
    acceptance = ledger.record_controlling_acceptance(
        subject_ref=independent,
        authority="owner",
        accepted=True,
    )
    p3 = p2.promote(
        "P3_CANONICAL",
        controlling_acceptance=acceptance,
        ledger=ledger,
    )
    assert p3 is not None
    assert p3.provenance_level == "P3_CANONICAL"
    assert p2.promote(
        "P3_CANONICAL", controlling_acceptance="owner-label", ledger=ledger
    ) is None


def test_terra_7_readback_and_experiment_validation_reject_shape_only_success(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    empty = tmp_path / "empty.json"
    empty.write_bytes(b"")
    empty_result = readback_artifact(empty, required=True)
    assert empty_result.passed is False
    assert empty_result.event == "SILENT_FAILURE_DETECTED"

    wrong = tmp_path / "wrong.json"
    wrong.write_text('{"status":"PASS","all_passed":false}', encoding="utf-8")
    wrong_result = readback_artifact(
        wrong,
        required=True,
        acceptance_contract={"status": "PASS", "all_passed": True},
    )
    assert wrong_result.passed is False

    shape_only = validate_experiment_receipt(
        {
            "run_id": "run-1",
            "invocation": ["python", "experiment.py"],
            "started_at": "2026-08-26T12:00:02Z",
            "ended_at": "2026-08-26T12:00:01Z",
            "exit_status": 0,
            "source_revision": "a" * 40,
            "input_sha256": {"input.json": "b" * 64},
            "output_sha256": {"output.json": "c" * 64},
            "runtime_envelope_sha256": "d" * 64,
            "tool_receipt_sha256": "e" * 64,
            "independent_review_receipt": "f" * 64,
        },
        ledger=RuntimeIntegrityLedger(),
    )
    assert shape_only.action_proven is False
    assert shape_only.publication_allowed is False
    assert "TIMESTAMP_ORDER" in shape_only.missing_evidence
    assert "TRUSTED_LEDGER_REFERENCES" in shape_only.missing_evidence


def test_terra_8_canary_drives_registered_durable_boundaries(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    from scripts.runtime_integrity_canary import run_canary

    result = run_canary(
        output_path=tmp_path / "canary.json",
        hermes_home=tmp_path / "home",
    )
    i1 = result["cases"]["I1"]["evidence"]
    i3 = result["cases"]["I3"]["evidence"]
    i5 = result["cases"]["I5"]["evidence"]
    i7 = result["cases"]["I7"]["evidence"]
    assert i1["middleware_registered"] is True
    assert i1["second_post_swap_request_filtered"] is True
    assert i1["cross_session_change_detected"] is True
    assert i1["durable_transition_sha256"]
    assert i1["owner_notice_emitted"] is True
    assert i3["actual_publication_hook_denied"] is True
    assert i3["unrelated_p1_experiment_denied"] is True
    assert i3["unrelated_p1_debate_denied"] is True
    assert i3["wrong_subject_publication_denied"] is True
    assert i3["mismatched_source_revision_denied"] is True
    assert i5["actual_memory_hook_denied"] is True
    assert i5["unrelated_p1_memory_denied"] is True
    assert i7["actual_finalization_readback_failed_closed"] is True
    assert i7["lifecycle_finalization_blocked"] is True
