"""Focused tests for the append-only candidate-memory core.

These tests intentionally stay on the local filesystem.  Candidate memory is
an explicit, inert seam: importing the module must not call a provider or
write a canonical source.
"""

from __future__ import annotations

import json
import hashlib
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import hermes_cli.candidate_memory as candidate_memory
from hermes_cli.candidate_memory import (
    EVENT_FIELDS,
    RELATION_FIELDS,
    CandidateMemoryStore,
    LedgerCorruptionError,
    ProposalError,
    SchemaError,
    ScopeError,
    SourceVerificationError,
    TruncatedTailError,
    canonical_json_bytes,
    canonical_sha256,
)


class CandidateMemoryCoreTests(unittest.TestCase):
    def make_store(self) -> tuple[TemporaryDirectory[str], CandidateMemoryStore]:
        directory = TemporaryDirectory()
        return directory, CandidateMemoryStore(directory.name)

    def add_event(self, store: CandidateMemoryStore, **kwargs: object) -> dict[str, object]:
        defaults: dict[str, object] = {
            "project_scope": "Yatima",
            "claim_or_event": "owner accepted the bounded decision",
            "created_at": "2026-08-28T12:00:00Z",
            "source_locators": ["handoff.md#decision"],
            "source_revisions": ["rev-1"],
            "source_hashes": ["hash-1"],
        }
        defaults.update(kwargs)
        return store.append_event("DECISION_ACCEPTED", **defaults)

    def test_canonical_fingerprint_and_schema_are_stable(self) -> None:
        left = {"b": 2, "a": "e\u0301"}
        right = {"a": "é", "b": 2}
        self.assertEqual(canonical_json_bytes(left), canonical_json_bytes(right))
        self.assertEqual(canonical_sha256(left), canonical_sha256(right))

        directory, store = self.make_store()
        with directory:
            first = self.add_event(store, claim_or_event="  owner accepted the bounded decision  ")
            persisted = store.read_events()
            self.assertEqual(persisted, [first])
            self.assertEqual(set(first), set(EVENT_FIELDS))
            identity = {
                field: first[field]
                for field in EVENT_FIELDS
                if field not in {"event_id", "event_fingerprint", "created_at"}
            }
            self.assertEqual(first["event_fingerprint"], canonical_sha256(identity))

    def test_duplicate_choice_b_retains_event_and_appends_relation(self) -> None:
        directory, store = self.make_store()
        with directory:
            first = self.add_event(store)
            second = self.add_event(store)
            self.assertNotEqual(first["event_id"], second["event_id"])
            self.assertEqual(first["event_fingerprint"], second["event_fingerprint"])
            self.assertEqual(len(store.read_events()), 2)
            relations = store.read_relations()
            self.assertEqual(len(relations), 1)
            relation = relations[0]
            self.assertEqual(set(relation), set(RELATION_FIELDS))
            self.assertEqual(relation["record_type"], "DUPLICATE_OF")
            self.assertEqual(relation["from_candidate_ids"], [second["event_id"]])
            self.assertEqual(relation["to_candidate_ids"], [first["event_id"]])

            view = store.query(project_scope="Yatima")
            self.assertEqual(view[1]["duplicate_of"], [first["event_id"]])

    def test_append_is_line_oriented_and_preserves_prior_bytes(self) -> None:
        directory, store = self.make_store()
        with directory:
            self.add_event(store, task_id="first")
            before = store.events_path.read_bytes()
            before_hash = store.ledger_snapshot()["events"]["sha256"]
            self.add_event(store, task_id="second")
            after = store.events_path.read_bytes()
            self.assertTrue(after.startswith(before))
            self.assertEqual(before_hash, hashlib.sha256(before).hexdigest())
            self.assertEqual(store.ledger_snapshot()["events"]["sha256"], hashlib.sha256(after).hexdigest())
            self.assertEqual(len(after.splitlines()), 2)
            self.assertTrue(all(json.loads(line) for line in after.splitlines()))

    def test_query_requires_scope_and_isolates_private_and_hidden(self) -> None:
        directory, store = self.make_store()
        with directory:
            public = self.add_event(store, task_id="public")
            private = self.add_event(store, task_id="private", privacy_class="owner-private")
            hidden_same_project = self.add_event(store, task_id="hidden", privacy_class="hidden-evaluation")
            hidden_scope = self.add_event(
                store,
                project_scope="hidden-evaluation",
                privacy_class="hidden-evaluation",
                task_id="hidden-scope",
            )
            factory = self.add_event(store, project_scope="Factory", task_id="factory")

            with self.assertRaises((ScopeError, SchemaError)):
                store.query()
            with self.assertRaises(ScopeError):
                store.query(project_scope="not-a-project")
            ordinary = store.query(project_scope="Yatima")
            self.assertEqual([row["event_id"] for row in ordinary], [public["event_id"]])
            private_view = store.query(project_scope="Yatima", include_private=True)
            self.assertEqual(
                {row["event_id"] for row in private_view}, {public["event_id"], private["event_id"]}
            )
            all_yatima = store.query(project_scope="Yatima", include_private=True, include_hidden_evaluation=True)
            self.assertEqual(
                {row["event_id"] for row in all_yatima},
                {public["event_id"], private["event_id"], hidden_same_project["event_id"]},
            )
            hidden = store.query(project_scope="hidden-evaluation", include_hidden_evaluation=True)
            self.assertEqual([row["event_id"] for row in hidden], [hidden_scope["event_id"]])
            self.assertEqual(store.query(project_scope="Factory")[0]["event_id"], factory["event_id"])

    def test_relations_are_scoped_and_drive_view_states(self) -> None:
        directory, store = self.make_store()
        with directory:
            contradicted = self.add_event(store, task_id="contradicted")
            contradicting = self.add_event(store, task_id="contradicting")
            superseded = self.add_event(store, task_id="superseded")
            accepted = self.add_event(store, task_id="accepted")
            store.append_relation("CONTRADICTS", [contradicted["event_id"]], [contradicting["event_id"]])
            store.append_relation("SUPERSEDES", [contradicting["event_id"]], [superseded["event_id"]])
            store.append_relation("ACCEPTED_FOR_UPDATE_REQUEST", [accepted["event_id"]], [contradicting["event_id"]])
            rows = {row["event_id"]: row for row in store.query(project_scope="Yatima")}
            self.assertEqual(rows[contradicted["event_id"]]["state"], "CONFLICTING")
            self.assertEqual(rows[contradicting["event_id"]]["state"], "CONFLICTING")
            self.assertEqual(rows[superseded["event_id"]]["state"], "SUPERSEDED")
            self.assertEqual(rows[accepted["event_id"]]["state"], "ACCEPTED_FOR_UPDATE_REQUEST")

            other = self.add_event(store, project_scope="Factory", task_id="other")
            with self.assertRaises(ScopeError):
                store.append_relation("SUPPORTS", [contradicted["event_id"]], [other["event_id"]])
            with self.assertRaises(LedgerCorruptionError):
                store.append_relation("SUPPORTS", ["missing"], [contradicted["event_id"]])
            relation_prefix = store.relations_path.read_bytes()
            store.append_relation("SUPPORTS", [accepted["event_id"]], [contradicted["event_id"]])
            self.assertTrue(store.relations_path.read_bytes().startswith(relation_prefix))

    def test_capture_test_only_is_explicit_and_narrow(self) -> None:
        directory, store = self.make_store()
        with directory:
            captured = store.capture_test_only(
                "TASK_COMPLETED",
                project_scope="Yatima",
                claim_or_event="synthetic task completed",
                test_only=True,
            )
            self.assertTrue(captured["test_only"])
            with self.assertRaises(SchemaError):
                store.capture_test_only(
                    "OWNER_CORRECTION",
                    project_scope="Yatima",
                    claim_or_event="not an allowed shadow event",
                    test_only=True,
                )
            with self.assertRaises(SchemaError):
                store.capture_test_only(
                    "SOURCE_CHANGED",
                    project_scope="Yatima",
                    claim_or_event="missing explicit test flag",
                )

    def test_truncated_tail_recovery_quarantines_without_rewriting_history(self) -> None:
        directory, store = self.make_store()
        with directory:
            self.add_event(store, task_id="durable")
            prefix = store.events_path.read_bytes()
            with store.events_path.open("ab") as handle:
                handle.write(b'{"truncated":')
            with self.assertRaises(TruncatedTailError) as raised:
                store.read_events()
            self.assertEqual(raised.exception.offset, len(prefix))
            receipt = store.recover_truncated_tail()
            self.assertEqual(receipt["action"], "quarantined_truncated_tail")
            recovered = store.read_events()
            self.assertEqual(len(recovered), 1)
            self.assertTrue(store.events_path.read_bytes().startswith(prefix))
            recovery_lines = store.events_path.with_name("events.jsonl.recovery.jsonl").read_bytes().splitlines()
            self.assertEqual(len(recovery_lines), 1)
            self.assertEqual(json.loads(recovery_lines[0])["action"], "quarantined_truncated_tail")

    def test_valid_tail_recovery_only_appends_newline(self) -> None:
        directory, store = self.make_store()
        with directory:
            event = self.add_event(store, task_id="valid-tail")
            original = store.events_path.read_bytes()
            self.assertTrue(original.endswith(b"\n"))
            store.events_path.write_bytes(original[:-1])
            with self.assertRaises(TruncatedTailError):
                store.read_events()
            receipt = store.recover_truncated_tail()
            self.assertEqual(receipt["action"], "sealed_missing_newline")
            self.assertEqual(store.read_events(), [event])
            repaired = store.events_path.read_bytes()
            self.assertEqual(repaired, original)

    def test_tail_recovery_requires_ledger_valid_canonical_records(self) -> None:
        invalid_kinds = ("scalar", "array", "noncanonical", "wrong_schema")

        def fragment(kind: str, canonical_record: bytes) -> bytes:
            if kind == "scalar":
                return b"1"
            if kind == "array":
                return b"[]"
            if kind == "noncanonical":
                return b" " + canonical_record
            return canonical_json_bytes({"schema_version": candidate_memory.SCHEMA_VERSION})

        for kind in invalid_kinds:
            with self.subTest(ledger="events", kind=kind):
                directory, store = self.make_store()
                with directory:
                    self.add_event(store, task_id=f"invalid-event-tail-{kind}")
                    prefix = store.events_path.read_bytes()
                    malformed = fragment(kind, prefix.splitlines()[0])
                    candidate_memory._append_bytes(store.events_path, malformed)
                    receipt = store.recover_truncated_tail("events")
                    self.assertEqual(receipt["action"], "quarantined_truncated_tail")
                    self.assertTrue(store.events_path.read_bytes().startswith(prefix + malformed))
                    self.assertEqual(len(store.read_events()), 1)

            with self.subTest(ledger="relations", kind=kind):
                directory, store = self.make_store()
                with directory:
                    first = self.add_event(store, task_id=f"invalid-relation-first-{kind}")
                    second = self.add_event(store, task_id=f"invalid-relation-second-{kind}")
                    relation = store.append_relation(
                        "SUPPORTS", [first["event_id"]], [second["event_id"]]
                    )
                    prefix = store.relations_path.read_bytes()
                    malformed = fragment(kind, prefix.splitlines()[0])
                    candidate_memory._append_bytes(store.relations_path, malformed)
                    receipt = store.recover_truncated_tail("relations")
                    self.assertEqual(receipt["action"], "quarantined_truncated_tail")
                    self.assertTrue(store.relations_path.read_bytes().startswith(prefix + malformed))
                    self.assertEqual(store.read_relations(), [relation])

            with self.subTest(ledger="transactions", kind=kind):
                directory, store = self.make_store()
                with directory:
                    original = self.add_event(store, task_id=f"invalid-transaction-tail-{kind}")
                    original_append_record = candidate_memory._append_record

                    def fail_before_commit(path: Path, record: dict[str, object]) -> None:
                        if (
                            path == store.transactions_path
                            and record.get("record_type") == "DUPLICATE_APPEND_COMMIT"
                        ):
                            raise OSError("injected failure before duplicate commit")
                        original_append_record(path, record)

                    with patch.object(candidate_memory, "_append_record", side_effect=fail_before_commit):
                        with self.assertRaises(OSError):
                            self.add_event(
                                store,
                                task_id=original["task_id"],
                                claim_or_event=original["claim_or_event"],
                            )
                    prefix = store.transactions_path.read_bytes()
                    malformed = fragment(kind, prefix.splitlines()[0])
                    candidate_memory._append_bytes(store.transactions_path, malformed)
                    with store._locked(reconcile=False):
                        pass
                    recovery_path = candidate_memory._recovery_path(store.transactions_path)
                    recovery = [json.loads(line) for line in recovery_path.read_bytes().splitlines()]
                    self.assertEqual(recovery[-1]["action"], "quarantined_truncated_tail")
                    self.assertTrue(store.transactions_path.read_bytes().startswith(prefix + malformed))
                    self.assertEqual(
                        sum(
                            event["event_fingerprint"] == original["event_fingerprint"]
                            for event in store.read_events()
                        ),
                        2,
                    )

    def test_valid_relation_tail_recovery_only_appends_newline(self) -> None:
        directory, store = self.make_store()
        with directory:
            first = self.add_event(store, task_id="valid-relation-tail-first")
            second = self.add_event(store, task_id="valid-relation-tail-second")
            relation = store.append_relation("SUPPORTS", [first["event_id"]], [second["event_id"]])
            original = store.relations_path.read_bytes()
            store.relations_path.write_bytes(original[:-1])
            receipt = store.recover_truncated_tail("relations")
            self.assertEqual(receipt["action"], "sealed_missing_newline")
            self.assertEqual(store.read_relations(), [relation])
            self.assertEqual(store.relations_path.read_bytes(), original)

    def test_schema_and_fingerprint_corruption_fails_closed(self) -> None:
        directory, store = self.make_store()
        with directory:
            self.add_event(store)
            record = json.loads(store.events_path.read_text(encoding="utf-8"))
            record["claim_or_event"] = "tampered"
            store.events_path.write_bytes(canonical_json_bytes(record) + b"\n")
            with self.assertRaises(LedgerCorruptionError):
                store.read_events()

            record["claim_or_event"] = "owner accepted the bounded decision"
            record["unexpected"] = True
            store.events_path.write_bytes(canonical_json_bytes(record) + b"\n")
            with self.assertRaises(SchemaError):
                store.read_events()

    def test_concurrent_appends_have_complete_unique_lines(self) -> None:
        directory, store = self.make_store()
        with directory:
            def append(index: int) -> dict[str, object]:
                return self.add_event(store, task_id=f"parallel-{index}", claim_or_event=f"event {index}")

            with ThreadPoolExecutor(max_workers=8) as executor:
                events = list(executor.map(append, range(24)))
            persisted = store.read_events()
            self.assertEqual(len(persisted), 24)
            self.assertEqual(len({event["event_id"] for event in persisted}), 24)
            self.assertEqual({event["event_id"] for event in persisted}, {event["event_id"] for event in events})
            self.assertTrue(all(line.endswith(b"\n") for line in store.events_path.read_bytes().splitlines(keepends=True)))

    def test_disposable_view_and_index_rebuild_from_authoritative_ledgers(self) -> None:
        directory, store = self.make_store()
        with directory:
            first = self.add_event(store, task_id="index-first")
            expected = store.rebuild_current_view(project_scope="Yatima")
            self.assertEqual([row["event_id"] for row in expected], [first["event_id"]])
            self.assertFalse(store.index_path.exists())

            rebuilt = store.rebuild_index(project_scope="Yatima")
            self.assertEqual(rebuilt, expected)
            self.assertEqual(store.read_index(project_scope="Yatima"), expected)
            first_index_bytes = store.index_path.read_bytes()

            self.add_event(store, task_id="index-second")
            with self.assertRaises(LedgerCorruptionError):
                store.read_index(project_scope="Yatima")
            latest = store.rebuild_view_index(project_scope="Yatima")
            self.assertNotEqual(store.index_path.read_bytes(), first_index_bytes)
            self.assertEqual(store.read_index(project_scope="Yatima"), latest)

            store.index_path.unlink()
            self.assertEqual(store.rebuild_current_view(project_scope="Yatima"), latest)
            with self.assertRaises(LedgerCorruptionError):
                store.read_index(project_scope="Yatima")

    def test_source_reopen_verifies_hash_and_revision_fail_closed(self) -> None:
        directory, store = self.make_store()
        with directory:
            source = Path(store.root) / "cited-source.md"
            source.write_text("synthetic source evidence\n", encoding="utf-8", newline="")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            event = self.add_event(
                store,
                task_id="source-bound",
                source_locators=[str(source) + "#decision"],
                source_revisions=[f"sha256:{digest}"],
                source_hashes=[digest],
            )
            reopened = store.reopen_sources(event)
            self.assertEqual(reopened[0]["locator"], str(source) + "#decision")
            self.assertEqual(reopened[0]["source_hash"], digest)
            self.assertEqual(reopened[0]["source_revision"], f"sha256:{digest}")

            with self.assertRaises(SourceVerificationError):
                store.reopen_sources({**event, "source_revisions": ["revision-not-a-digest"]})
            source.write_text("tampered source evidence\n", encoding="utf-8", newline="")
            with self.assertRaises(SourceVerificationError):
                store.verify_source_binding(event)
            with self.assertRaises(SourceVerificationError):
                store.reopen_sources({**event, "source_locators": ["https://example.invalid/source"]})

    def test_proposal_bridge_is_dry_run_source_bound_and_state_aware(self) -> None:
        directory, store = self.make_store()
        with directory:
            source = Path(store.root) / "proposal-source.txt"
            source.write_text("proposal evidence\n", encoding="utf-8", newline="")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            candidate = self.add_event(
                store,
                task_id="proposal",
                source_locators=[str(source)],
                source_revisions=[digest],
                source_hashes=[digest],
            )
            before = store.ledger_snapshot()
            proposal = store.build_memory_update_request(
                [candidate["event_id"]],
                project_scope="Yatima",
            )
            after = store.ledger_snapshot()
            self.assertEqual(before, after)
            self.assertEqual(proposal["proposal_type"], "memory_update_request")
            self.assertTrue(proposal["dry_run"])
            self.assertEqual(proposal["canonical_write_count"], 0)
            self.assertEqual(proposal["model_calls"], 0)
            self.assertEqual(proposal["candidate_ids"], [candidate["event_id"]])
            self.assertEqual(proposal["evidence_lineage"][0]["candidate_id"], candidate["event_id"])
            self.assertEqual(proposal["evidence_lineage"][0]["reopened_sources"][0]["source_hash"], digest)
            self.assertEqual(proposal["explicit_relations"], [])

            blocked = self.add_event(store, task_id="proposal-blocked", stale=True)
            with self.assertRaises(ProposalError):
                store.build_proposal([blocked["event_id"]], project_scope="Yatima")
            with self.assertRaises(ScopeError):
                store.propose_memory_update([candidate["event_id"]], project_scope="Factory")

    def test_proposal_is_read_only_and_fails_closed_for_pending_duplicate_stages(self) -> None:
        for failure_stage in ("before_event", "between_event_and_relation", "before_commit"):
            with self.subTest(failure_stage=failure_stage):
                directory, store = self.make_store()
                with directory:
                    source = Path(store.root) / "proposal-read-only-source.txt"
                    source.write_text("proposal read-only evidence\n", encoding="utf-8", newline="")
                    digest = hashlib.sha256(source.read_bytes()).hexdigest()
                    original = self.add_event(
                        store,
                        task_id=f"proposal-read-only-{failure_stage}",
                        source_locators=[str(source)],
                        source_revisions=[digest],
                        source_hashes=[digest],
                    )
                    store.rebuild_index(project_scope="Yatima")
                    sentinel = Path(store.root) / "canonical-sentinel"
                    sentinel.write_bytes(b"canonical state is outside candidate memory\n")

                    def snapshot() -> dict[str, bytes]:
                        paths = {
                            "events": store.events_path,
                            "relations": store.relations_path,
                            "transactions": store.transactions_path,
                            "events_recovery": candidate_memory._recovery_path(store.events_path),
                            "relations_recovery": candidate_memory._recovery_path(store.relations_path),
                            "transactions_recovery": candidate_memory._recovery_path(store.transactions_path),
                            "index": store.index_path,
                            "canonical_sentinel": sentinel,
                        }
                        return {
                            name: path.read_bytes() if path.exists() else b""
                            for name, path in paths.items()
                        }

                    original_append_record = candidate_memory._append_record

                    def fail_once(path: Path, record: dict[str, object]) -> None:
                        if failure_stage == "before_event" and path == store.events_path:
                            raise OSError("injected failure before event append")
                        if failure_stage == "between_event_and_relation" and path == store.relations_path:
                            raise OSError("injected failure between event and relation append")
                        if (
                            failure_stage == "before_commit"
                            and path == store.transactions_path
                            and record.get("record_type") == "DUPLICATE_APPEND_COMMIT"
                        ):
                            raise OSError("injected failure before duplicate commit")
                        original_append_record(path, record)

                    duplicate_kwargs = {
                        "task_id": original["task_id"],
                        "claim_or_event": original["claim_or_event"],
                        "source_locators": original["source_locators"],
                        "source_revisions": original["source_revisions"],
                        "source_hashes": original["source_hashes"],
                    }
                    with patch.object(candidate_memory, "_append_record", side_effect=fail_once):
                        with self.assertRaises(OSError):
                            self.add_event(store, **duplicate_kwargs)

                    before = snapshot()
                    with self.assertRaises(LedgerCorruptionError):
                        store.build_memory_update_request(
                            [original["event_id"]],
                            project_scope="Yatima",
                        )
                    self.assertEqual(snapshot(), before)

    def test_proposal_rejects_truncated_transaction_without_recovery_write(self) -> None:
        directory, store = self.make_store()
        with directory:
            source = Path(store.root) / "proposal-truncated-source.txt"
            source.write_text("proposal truncated evidence\n", encoding="utf-8", newline="")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            candidate = self.add_event(
                store,
                task_id="proposal-truncated",
                source_locators=[str(source)],
                source_revisions=[digest],
                source_hashes=[digest],
            )
            store.rebuild_index(project_scope="Yatima")
            sentinel = Path(store.root) / "canonical-sentinel"
            sentinel.write_bytes(b"canonical state is outside candidate memory\n")
            candidate_memory._append_bytes(
                store.transactions_path,
                b'{"record_type":"DUPLICATE_APPEND_INTENT"',
            )
            paths = (
                store.events_path,
                store.relations_path,
                store.transactions_path,
                candidate_memory._recovery_path(store.events_path),
                candidate_memory._recovery_path(store.relations_path),
                candidate_memory._recovery_path(store.transactions_path),
                store.index_path,
                sentinel,
            )
            before = {
                path: path.read_bytes() if path.exists() else b""
                for path in paths
            }
            with self.assertRaises(TruncatedTailError):
                store.build_memory_update_request([candidate["event_id"]], project_scope="Yatima")
            self.assertEqual(
                {path: path.read_bytes() if path.exists() else b"" for path in paths},
                before,
            )

    def test_quarantined_malformed_line_remains_skippable_after_later_append(self) -> None:
        directory, store = self.make_store()
        with directory:
            durable = self.add_event(store, task_id="quarantine-durable")
            before_fragment = store.events_path.read_bytes()
            malformed = b'{"malformed":"quarantined"'
            candidate_memory._append_bytes(store.events_path, malformed)
            malformed_bytes = store.events_path.read_bytes()
            receipt = store.recover_truncated_tail("events")
            self.assertEqual(receipt["action"], "quarantined_truncated_tail")
            recovered_bytes = store.events_path.read_bytes()
            self.assertTrue(recovered_bytes.startswith(malformed_bytes))
            recovery_path = candidate_memory._recovery_path(store.events_path)
            recovery_bytes = recovery_path.read_bytes()

            appended = self.add_event(store, task_id="quarantine-appended")
            events = store.read_events()
            self.assertEqual([event["event_id"] for event in events], [durable["event_id"], appended["event_id"]])
            self.assertTrue(store.events_path.read_bytes().startswith(recovered_bytes))
            self.assertEqual(recovery_path.read_bytes(), recovery_bytes)

    def test_transaction_quarantine_then_reconciliation_commits_without_rewriting_prefix(self) -> None:
        directory, store = self.make_store()
        with directory:
            original = self.add_event(store, task_id="transaction-quarantine-original")
            original_append_record = candidate_memory._append_record

            def fail_before_commit(path: Path, record: dict[str, object]) -> None:
                if path == store.transactions_path and record.get("record_type") == "DUPLICATE_APPEND_COMMIT":
                    raise OSError("injected failure before duplicate commit")
                original_append_record(path, record)

            with patch.object(candidate_memory, "_append_record", side_effect=fail_before_commit):
                with self.assertRaises(OSError):
                    self.add_event(
                        store,
                        task_id=original["task_id"],
                        claim_or_event=original["claim_or_event"],
                    )
            malformed = b'{"record_type":"DUPLICATE_APPEND_INTENT"'
            candidate_memory._append_bytes(store.transactions_path, malformed)
            prior_transaction_bytes = store.transactions_path.read_bytes()
            events_before = store.events_path.read_bytes()
            relations_before = store.relations_path.read_bytes()

            events = store.read_events()
            relations = store.read_relations()
            self.assertEqual(
                sum(event["event_fingerprint"] == original["event_fingerprint"] for event in events),
                2,
            )
            self.assertEqual(sum(relation["record_type"] == "DUPLICATE_OF" for relation in relations), 1)
            self.assertTrue(store.transactions_path.read_bytes().startswith(prior_transaction_bytes))
            self.assertTrue(store.events_path.read_bytes().startswith(events_before))
            self.assertTrue(store.relations_path.read_bytes().startswith(relations_before))
            recovery_path = candidate_memory._recovery_path(store.transactions_path)
            recovery = [json.loads(line) for line in recovery_path.read_bytes().splitlines()]
            self.assertEqual(recovery[-1]["action"], "quarantined_truncated_tail")

    def test_recovery_receipt_must_bind_to_its_data_ledger(self) -> None:
        directory, store = self.make_store()
        with directory:
            self.add_event(store, task_id="cross-ledger-receipt")
            candidate_memory._append_bytes(store.events_path, b'{"malformed":"events"')
            store.recover_truncated_tail("events")
            recovery_path = candidate_memory._recovery_path(store.events_path)
            receipt = json.loads(recovery_path.read_bytes().splitlines()[0])
            receipt["ledger"] = store.relations_path.name
            receipt["recovery_id"] = canonical_sha256(
                {
                    "ledger": receipt["ledger"],
                    "offset": receipt["offset"],
                    "fragment_sha256": receipt["fragment_sha256"],
                    "action": receipt["action"],
                }
            )
            recovery_path.write_bytes(canonical_json_bytes(receipt) + b"\n")
            with self.assertRaises(LedgerCorruptionError):
                store.read_events()

    def test_read_index_rejects_noncanonical_persisted_bytes(self) -> None:
        directory, store = self.make_store()
        with directory:
            self.add_event(store, task_id="index-canonical")
            store.rebuild_index(project_scope="Yatima")
            canonical = store.index_path.read_bytes()
            store.index_path.write_bytes(b" " + canonical)
            with self.assertRaises(LedgerCorruptionError):
                store.read_index(project_scope="Yatima")

            store.index_path.write_bytes(canonical)
            payload = json.loads(canonical.decode("utf-8"))
            reordered = {key: payload[key] for key in reversed(list(payload))}
            store.index_path.write_bytes(json.dumps(reordered, separators=(",", ":")).encode("utf-8"))
            with self.assertRaises(LedgerCorruptionError):
                store.read_index(project_scope="Yatima")

    def test_full_public_synthetic_matrix_and_shadow_capture(self) -> None:
        directory, store = self.make_store()
        with directory:
            source = Path(store.root) / "synthetic-evidence.txt"
            source.write_text("public synthetic evidence\n", encoding="utf-8", newline="")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            common = {
                "source_locators": [str(source)],
                "source_revisions": [digest],
                "source_hashes": [digest],
            }

            def add(event_type: str, project: str = "Yatima", index: int = 0, **kwargs: object) -> dict[str, object]:
                values: dict[str, object] = {
                    "project_scope": project,
                    "claim_or_event": f"public synthetic {event_type.lower()} {project} {index}",
                    "task_id": f"synthetic-{project}-{event_type}-{index}",
                    "created_at": "2026-08-28T13:00:00Z",
                }
                values.update(kwargs)
                return store.append_event(event_type, **values)

            owner = add("OWNER_CORRECTION")
            decisions = [add("DECISION_ACCEPTED", index=i) for i in range(2)]
            blockers = [add("BLOCKER_CHANGED", index=i) for i in range(2)]
            sources = [add("SOURCE_CHANGED", index=i, **common) for i in range(2)]
            tasks = [add("TASK_COMPLETED", index=i) for i in range(2)]
            authorization = add("AUTHORIZATION_CHANGED")
            route = add("MODEL_ROUTE_CHANGED")
            milestone = add("MILESTONE_REACHED")
            explicit = add("EXPLICIT_MEMORY_CAPTURE", **common)
            duplicate_one = add(
                "DECISION_ACCEPTED",
                index=2,
                claim_or_event=decisions[0]["claim_or_event"],
                task_id=decisions[0]["task_id"],
            )
            duplicate_two = add(
                "DECISION_ACCEPTED",
                index=3,
                claim_or_event=decisions[0]["claim_or_event"],
                task_id=decisions[0]["task_id"],
            )
            contradicted = add("DECISION_ACCEPTED", index=4)
            superseded = add("DECISION_ACCEPTED", index=5)
            stale = add("TASK_COMPLETED", index=2, stale=True)
            cancelled = add("TASK_COMPLETED", index=3, cancelled=True)
            hidden = add(
                "EXPLICIT_MEMORY_CAPTURE",
                project="hidden-evaluation",
                index=0,
                privacy_class="hidden-evaluation",
            )
            factory = add("TASK_COMPLETED", project="Factory", index=0)
            self.assertTrue(owner["event_id"] and authorization["event_id"] and route["event_id"])
            self.assertTrue(milestone["event_id"] and explicit["event_id"] and sources[0]["event_id"])

            contradiction_relation = store.append_relation(
                "CONTRADICTS", [decisions[0]["event_id"]], [contradicted["event_id"]]
            )
            supersession_relation = store.append_relation(
                "SUPERSEDES", [decisions[1]["event_id"]], [superseded["event_id"]]
            )
            self.assertEqual(contradiction_relation["record_type"], "CONTRADICTS")
            self.assertEqual(supersession_relation["record_type"], "SUPERSEDES")

            counts = Counter(event["event_type"] for event in store.read_events())
            self.assertGreaterEqual(counts["OWNER_CORRECTION"], 1)
            for event_type in (
                "DECISION_ACCEPTED",
                "BLOCKER_CHANGED",
                "SOURCE_CHANGED",
                "TASK_COMPLETED",
            ):
                self.assertGreaterEqual(counts[event_type], 2)
            for event_type in ("AUTHORIZATION_CHANGED", "MODEL_ROUTE_CHANGED", "MILESTONE_REACHED", "EXPLICIT_MEMORY_CAPTURE"):
                self.assertGreaterEqual(counts[event_type], 1)
            self.assertEqual(len(store.read_relations()), 4)
            self.assertEqual(store.query(project_scope="Factory")[0]["event_id"], factory["event_id"])
            self.assertFalse(any(row["event_id"] == hidden["event_id"] for row in store.query(project_scope="Yatima")))
            self.assertEqual(store.query(project_scope="hidden-evaluation"), [])
            self.assertEqual(
                store.query(project_scope="hidden-evaluation", include_hidden_evaluation=True)[0]["event_id"],
                hidden["event_id"],
            )
            self.assertEqual(
                sorted(row["state"] for row in store.query(project_scope="Yatima") if row["event_id"] in {
                    decisions[0]["event_id"], contradicted["event_id"]
                }),
                ["CONFLICTING", "CONFLICTING"],
            )
            view = {row["event_id"]: row for row in store.rebuild_current_view(project_scope="Yatima")}
            self.assertEqual(view[superseded["event_id"]]["state"], "SUPERSEDED")
            self.assertEqual(view[stale["event_id"]]["state"], "STALE")
            self.assertEqual(view[cancelled["event_id"]]["state"], "CANCELLED")
            self.assertEqual(view[duplicate_one["event_id"]]["duplicate_of"], [decisions[0]["event_id"]])
            self.assertEqual(view[duplicate_two["event_id"]]["duplicate_of"], [duplicate_one["event_id"]])

            # The four real-path shadow event classes remain an explicit,
            # test-only seam; duplicate injection still follows choice B.
            shadow_events: list[dict[str, object]] = []
            for index, event_type in enumerate(
                ("TASK_COMPLETED", "MODEL_ROUTE_CHANGED", "SOURCE_CHANGED", "EXPLICIT_MEMORY_CAPTURE")
            ):
                shadow_events.append(
                    store.capture_test_only(
                        event_type,
                        project_scope="Yatima",
                        claim_or_event=f"shadow {event_type} {index}",
                        task_id=f"shadow-{index}",
                        test_only=True,
                        source_locators=[str(source)],
                        source_revisions=[digest],
                        source_hashes=[digest],
                    )
                )
            duplicate_shadow = store.capture_test_only(
                "TASK_COMPLETED",
                project_scope="Yatima",
                claim_or_event="shadow TASK_COMPLETED 0",
                task_id="shadow-0",
                test_only=True,
                source_locators=[str(source)],
                source_revisions=[digest],
                source_hashes=[digest],
            )
            self.assertEqual(len(shadow_events), 4)
            self.assertNotEqual(duplicate_shadow["event_id"], shadow_events[0]["event_id"])
            self.assertEqual(
                store.query(project_scope="Yatima")[-1]["duplicate_of"], [shadow_events[0]["event_id"]]
            )
            before = store.ledger_snapshot()
            proposal = store.build_memory_update_request(
                [shadow_events[1]["event_id"]], project_scope="Yatima"
            )
            self.assertEqual(before, store.ledger_snapshot())
            self.assertEqual(proposal["canonical_write_count"], 0)
            self.assertEqual(proposal["model_calls"], 0)

    def test_store_root_must_be_explicit_absolute_path(self) -> None:
        with self.assertRaises(SchemaError):
            CandidateMemoryStore("")
        with self.assertRaises(SchemaError):
            CandidateMemoryStore("relative-candidate-memory")

    def test_read_only_operations_never_create_or_repair_store_lock(self) -> None:
        with TemporaryDirectory() as parent:
            root = Path(parent) / "never-created"
            store = CandidateMemoryStore(root)
            for operation in (
                lambda: store.ledger_snapshot(),
                lambda: store.build_memory_update_request(
                    ["candidate-missing"], project_scope="Yatima"
                ),
            ):
                with self.assertRaises(LedgerCorruptionError):
                    operation()
                self.assertFalse(root.exists())
                self.assertFalse(store.lock_path.exists())

        directory, store = self.make_store()
        with directory:
            source = Path(store.root) / "read-only-lock-source.txt"
            source.write_text("read-only lock evidence\n", encoding="utf-8", newline="")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            candidate = self.add_event(
                store,
                task_id="read-only-lock",
                source_locators=[str(source)],
                source_revisions=[digest],
                source_hashes=[digest],
            )
            store.rebuild_index(project_scope="Yatima")

            def snapshot_files() -> dict[str, bytes]:
                return {
                    path.relative_to(store.root).as_posix(): path.read_bytes()
                    for path in store.root.rglob("*")
                    if path.is_file()
                }

            before = snapshot_files()
            self.assertEqual(store.lock_path.read_bytes(), b"\0")
            proposal = store.build_memory_update_request(
                [candidate["event_id"]], project_scope="Yatima"
            )
            self.assertEqual(proposal["canonical_write_count"], 0)
            self.assertEqual(store.ledger_snapshot()["events"]["sha256"], hashlib.sha256(
                store.events_path.read_bytes()
            ).hexdigest())
            self.assertEqual(snapshot_files(), before)

            for invalid_lock in (None, b"x", b"\0\0"):
                if invalid_lock is None:
                    store.lock_path.unlink()
                else:
                    store.lock_path.write_bytes(invalid_lock)
                unchanged = snapshot_files()
                for operation in (
                    lambda: store.ledger_snapshot(),
                    lambda: store.build_memory_update_request(
                        [candidate["event_id"]], project_scope="Yatima"
                    ),
                ):
                    with self.assertRaises(LedgerCorruptionError):
                        operation()
                    self.assertEqual(snapshot_files(), unchanged)
                if invalid_lock is None:
                    self.assertFalse(store.lock_path.exists())
                else:
                    self.assertEqual(store.lock_path.read_bytes(), invalid_lock)

    def test_quarantined_transaction_tail_permanently_blocks_read_only_proposals(self) -> None:
        directory, store = self.make_store()
        with directory:
            source = Path(store.root) / "permanent-transaction-quarantine.txt"
            source.write_text("permanent transaction quarantine evidence\n", encoding="utf-8", newline="")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            original = self.add_event(
                store,
                task_id="permanent-transaction-quarantine",
                source_locators=[str(source)],
                source_revisions=[digest],
                source_hashes=[digest],
            )
            store.rebuild_index(project_scope="Yatima")
            candidate_memory._append_bytes(
                store.transactions_path,
                b'{"record_type":"DUPLICATE_APPEND_INTENT"',
            )
            store.read_events()  # Ordinary recovery quarantines the malformed tail.
            self.add_event(
                store,
                task_id=original["task_id"],
                claim_or_event=original["claim_or_event"],
                source_locators=[str(source)],
                source_revisions=[digest],
                source_hashes=[digest],
            )
            self.assertEqual(len(store.read_relations()), 1)
            recovery = [
                json.loads(line)
                for line in candidate_memory._recovery_path(store.transactions_path).read_bytes().splitlines()
            ]
            self.assertEqual(recovery[-1]["action"], "quarantined_truncated_tail")

            paths = {
                path
                for path in store.root.rglob("*")
                if path.is_file()
            }
            before = {path: path.read_bytes() for path in paths}
            for operation in (
                lambda: store.build_memory_update_request(
                    [original["event_id"]], project_scope="Yatima"
                ),
                lambda: store.ledger_snapshot(),
            ):
                with self.assertRaises(LedgerCorruptionError):
                    operation()
                self.assertEqual({path: path.read_bytes() for path in paths}, before)

    def test_public_duplicate_relation_is_reserved_and_injected_duplicates_fail_closed(self) -> None:
        directory, store = self.make_store()
        with directory:
            first = self.add_event(store, task_id="public-duplicate-relation-first")
            with self.assertRaises(SchemaError):
                store.append_relation(
                    "DUPLICATE_OF", [first["event_id"]], [first["event_id"]]
                )
            self.assertFalse(store.relations_path.exists())

        for corruption in ("orphan", "self", "wrong_fingerprint"):
            with self.subTest(corruption=corruption):
                directory, store = self.make_store()
                with directory:
                    first = self.add_event(store, task_id=f"injected-{corruption}-first")
                    if corruption == "orphan":
                        # Persist a second identical event without its
                        # duplicate transaction, then inject only its relation.
                        second = dict(first)
                        second["event_id"] = candidate_memory._expected_event_id(second, 2)
                        candidate_memory._append_record(store.events_path, second)
                        target_id = first["event_id"]
                        source_id = second["event_id"]
                    elif corruption == "self":
                        source_id = target_id = first["event_id"]
                    else:
                        second = self.add_event(store, task_id=f"injected-{corruption}-second")
                        source_id = first["event_id"]
                        target_id = second["event_id"]
                    relation = store._make_relation_unlocked(
                        "DUPLICATE_OF",
                        [source_id],
                        [target_id],
                        reviewer_identity="test-injection",
                        source_locators=[],
                        source_hashes=[],
                        reason=f"injected {corruption}",
                        existing_relations=[],
                    )
                    candidate_memory._append_record(store.relations_path, relation)

                    for operation in (
                        lambda: store.read_events(),
                        lambda: store.read_relations(),
                        lambda: store.query(project_scope="Yatima"),
                        lambda: store.build_memory_update_request(
                            [first["event_id"]], project_scope="Yatima"
                        ),
                    ):
                        with self.assertRaises(LedgerCorruptionError):
                            operation()

    def test_orphan_duplicate_event_without_transaction_or_relation_fails_closed(self) -> None:
        directory, store = self.make_store()
        with directory:
            first = self.add_event(store, task_id="orphan-duplicate-event")
            second = dict(first)
            second["event_id"] = candidate_memory._expected_event_id(second, 2)
            candidate_memory._append_record(store.events_path, second)

            for operation in (
                lambda: store.read_events(),
                lambda: store.read_relations(),
                lambda: store.query(project_scope="Yatima"),
                lambda: store.build_memory_update_request(
                    [first["event_id"]], project_scope="Yatima"
                ),
                lambda: store.ledger_snapshot(),
            ):
                with self.assertRaises(LedgerCorruptionError):
                    operation()

    def test_duplicate_event_relation_must_target_immediate_predecessor(self) -> None:
        directory, store = self.make_store()
        with directory:
            first = self.add_event(store, task_id="wrong-duplicate-target")
            second = dict(first)
            second["event_id"] = candidate_memory._expected_event_id(second, 2)
            third = dict(first)
            third["event_id"] = candidate_memory._expected_event_id(third, 3)
            candidate_memory._append_record(store.events_path, second)
            candidate_memory._append_record(store.events_path, third)

            # Both injected relations are durably backed by valid-looking
            # choice-B transactions, but the second occurrence points forward
            # to occurrence three instead of its immediate predecessor.
            second_relation = store._make_relation_unlocked(
                "DUPLICATE_OF",
                [second["event_id"]],
                [third["event_id"]],
                reviewer_identity="test-injection",
                source_locators=[],
                source_hashes=[],
                reason="wrong duplicate target",
                existing_relations=[],
            )
            third_relation = store._make_relation_unlocked(
                "DUPLICATE_OF",
                [third["event_id"]],
                [second["event_id"]],
                reviewer_identity="test-injection",
                source_locators=[],
                source_hashes=[],
                reason="third duplicate target",
                existing_relations=[second_relation],
            )
            for event, relation in ((second, second_relation), (third, third_relation)):
                candidate_memory._append_record(store.relations_path, relation)
                transaction_id = candidate_memory._transaction_id(event, relation)
                candidate_memory._append_record(
                    store.transactions_path,
                    {
                        "schema_version": candidate_memory.SCHEMA_VERSION,
                        "transaction_id": transaction_id,
                        "record_type": "DUPLICATE_APPEND_INTENT",
                        "event": event,
                        "relation": relation,
                        "created_at": "2026-08-28T12:01:00Z",
                    },
                )
                candidate_memory._append_record(
                    store.transactions_path,
                    {
                        **store._duplicate_transaction_commit(transaction_id, event, relation),
                        "created_at": "2026-08-28T12:01:00Z",
                    },
                )

            with self.assertRaises(LedgerCorruptionError):
                store.read_events()

    def test_proposal_requires_verified_sources_and_never_leaks_private_or_hidden_relations(self) -> None:
        directory, store = self.make_store()
        with directory:
            source = Path(store.root) / "proposal-bound-source.txt"
            source.write_text("proposal-bound evidence\n", encoding="utf-8", newline="")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            source_fields = {
                "source_locators": [str(source)],
                "source_revisions": [f"sha256:{digest}"],
                "source_hashes": [digest],
            }
            public = self.add_event(store, task_id="strict-public", **source_fields)
            no_source = self.add_event(
                store,
                task_id="strict-no-source",
                source_locators=[],
                source_revisions=[],
                source_hashes=[],
            )
            private = self.add_event(store, task_id="strict-private", privacy_class="owner-private", **source_fields)
            hidden_one = self.add_event(
                store,
                task_id="strict-hidden-one",
                privacy_class="hidden-evaluation",
                **source_fields,
            )
            hidden_two = self.add_event(
                store,
                task_id="strict-hidden-two",
                privacy_class="hidden-evaluation",
                **source_fields,
            )
            store.append_relation("SUPPORTS", [hidden_one["event_id"]], [hidden_two["event_id"]])

            with self.assertRaises(ProposalError):
                store.build_memory_update_request([no_source["event_id"]], project_scope="Yatima")
            with self.assertRaises(ScopeError):
                store.build_memory_update_request([private["event_id"]], project_scope="Yatima")
            with self.assertRaises(ScopeError):
                store.append_relation("SUPPORTS", [public["event_id"]], [private["event_id"]])

            related = self.add_event(store, task_id="strict-related", **source_fields)
            unrelated = self.add_event(store, task_id="strict-unrelated", **source_fields)
            unrelated_peer = self.add_event(store, task_id="strict-unrelated-peer", **source_fields)
            related_relation = store.append_relation(
                "SUPPORTS", [public["event_id"]], [related["event_id"]]
            )
            unrelated_relation = store.append_relation(
                "SUPPORTS", [unrelated["event_id"]], [unrelated_peer["event_id"]]
            )
            proposal = store.build_memory_update_request([public["event_id"]], project_scope="Yatima")
            self.assertEqual(proposal["candidate_ids"], [public["event_id"]])
            self.assertEqual(
                [relation["record_id"] for relation in proposal["explicit_relations"]],
                [related_relation["record_id"]],
            )
            self.assertNotIn(unrelated_relation["record_id"], {
                relation["record_id"] for relation in proposal["explicit_relations"]
            })
            proposal_text = json.dumps(proposal, sort_keys=True)
            self.assertNotIn(private["event_id"], proposal_text)
            self.assertNotIn(hidden_one["event_id"], proposal_text)
            self.assertNotIn(hidden_two["event_id"], proposal_text)
            self.assertNotIn(unrelated["event_id"], proposal_text)
            self.assertNotIn(unrelated_peer["event_id"], proposal_text)

    def test_duplicate_transaction_reconciles_each_fault_boundary_without_rewriting_ledgers(self) -> None:
        for failure_stage in ("before_event", "between_event_and_relation", "before_commit"):
            with self.subTest(failure_stage=failure_stage):
                directory, store = self.make_store()
                with directory:
                    original = self.add_event(store, task_id=f"txn-original-{failure_stage}")
                    event_prefix = store.events_path.read_bytes()
                    relation_prefix = store.relations_path.read_bytes() if store.relations_path.exists() else b""
                    original_append_record = candidate_memory._append_record

                    def fail_once(path: Path, record: dict[str, object]) -> None:
                        if failure_stage == "before_event" and path == store.events_path:
                            raise OSError("injected failure before event append")
                        if failure_stage == "between_event_and_relation" and path == store.relations_path:
                            raise OSError("injected failure between event and relation append")
                        if (
                            failure_stage == "before_commit"
                            and path == store.transactions_path
                            and record.get("record_type") == "DUPLICATE_APPEND_COMMIT"
                        ):
                            raise OSError("injected failure before duplicate commit")
                        original_append_record(path, record)

                    duplicate_kwargs = {
                        field: original[field]
                        for field in EVENT_FIELDS
                        if field not in {"schema_version", "event_id", "event_type", "event_fingerprint", "created_at"}
                    }
                    with patch.object(candidate_memory, "_append_record", side_effect=fail_once):
                        with self.assertRaises(OSError):
                            self.add_event(store, **duplicate_kwargs)

                    recovered = store.read_events()
                    recovered_relations = store.read_relations()
                    matching = [event for event in recovered if event["event_fingerprint"] == original["event_fingerprint"]]
                    self.assertEqual(len(matching), 2)
                    duplicate_relations = [relation for relation in recovered_relations if relation["record_type"] == "DUPLICATE_OF"]
                    self.assertEqual(len(duplicate_relations), 1)
                    self.assertEqual(duplicate_relations[0]["from_candidate_ids"], [matching[1]["event_id"]])
                    self.assertEqual(duplicate_relations[0]["to_candidate_ids"], [matching[0]["event_id"]])
                    self.assertTrue(store.events_path.read_bytes().startswith(event_prefix))
                    self.assertTrue(
                        (store.relations_path.read_bytes() if store.relations_path.exists() else b"").startswith(
                            relation_prefix
                        )
                    )
                    journal = store.transactions_path.read_bytes().splitlines()
                    self.assertEqual(len(journal), 2)
                    self.assertEqual(json.loads(journal[0])["record_type"], "DUPLICATE_APPEND_INTENT")
                    self.assertEqual(json.loads(journal[1])["record_type"], "DUPLICATE_APPEND_COMMIT")

    def test_recover_selected_duplicate_tails_before_reconciliation(self) -> None:
        for ledger in ("events", "relations"):
            with self.subTest(ledger=ledger):
                directory, store = self.make_store()
                with directory:
                    original = self.add_event(store, task_id=f"selected-tail-{ledger}")
                    event_prefix = store.events_path.read_bytes()
                    relation_prefix = store.relations_path.read_bytes() if store.relations_path.exists() else b""
                    original_append_record = candidate_memory._append_record

                    def fail_at_boundary(path: Path, record: dict[str, object]) -> None:
                        if ledger == "events" and path == store.relations_path:
                            raise OSError("injected failure before duplicate relation")
                        if (
                            ledger == "relations"
                            and path == store.transactions_path
                            and record.get("record_type") == "DUPLICATE_APPEND_COMMIT"
                        ):
                            raise OSError("injected failure before duplicate commit")
                        original_append_record(path, record)

                    duplicate_kwargs = {
                        field: original[field]
                        for field in EVENT_FIELDS
                        if field not in {"schema_version", "event_id", "event_type", "event_fingerprint", "created_at"}
                    }
                    with patch.object(candidate_memory, "_append_record", side_effect=fail_at_boundary):
                        with self.assertRaises(OSError):
                            store.append_event("DECISION_ACCEPTED", **duplicate_kwargs)

                    if ledger == "events":
                        selected_path = store.events_path
                    else:
                        selected_path = store.relations_path
                    selected_before = selected_path.read_bytes()
                    self.assertTrue(selected_before.endswith(b"\n"))
                    selected_tail = selected_before[:-1]
                    selected_path.write_bytes(selected_tail)

                    receipt = store.recover_truncated_tail(ledger)
                    self.assertEqual(receipt["action"], "sealed_missing_newline")
                    self.assertTrue(selected_path.read_bytes().startswith(selected_tail))

                    events = store.read_events()
                    relations = store.read_relations()
                    matching = [
                        event
                        for event in events
                        if event["event_fingerprint"] == original["event_fingerprint"]
                    ]
                    self.assertEqual(len(matching), 2)
                    duplicate_relations = [
                        relation for relation in relations if relation["record_type"] == "DUPLICATE_OF"
                    ]
                    self.assertEqual(len(duplicate_relations), 1)
                    self.assertEqual(
                        duplicate_relations[0]["from_candidate_ids"], [matching[1]["event_id"]]
                    )
                    self.assertEqual(
                        duplicate_relations[0]["to_candidate_ids"], [matching[0]["event_id"]]
                    )
                    journal = store.transactions_path.read_bytes().splitlines()
                    self.assertEqual(len(journal), 2)
                    self.assertEqual(json.loads(journal[0])["record_type"], "DUPLICATE_APPEND_INTENT")
                    self.assertEqual(json.loads(journal[1])["record_type"], "DUPLICATE_APPEND_COMMIT")
                    self.assertTrue(store.events_path.read_bytes().startswith(event_prefix))
                    self.assertTrue(store.relations_path.read_bytes().startswith(relation_prefix))

    def test_recover_malformed_selected_tail_does_not_reconcile_corrupt_intent(self) -> None:
        directory, store = self.make_store()
        with directory:
            original = self.add_event(store, task_id="malformed-selected-tail")
            event_prefix = store.events_path.read_bytes()
            relation_prefix = store.relations_path.read_bytes() if store.relations_path.exists() else b""
            original_append_record = candidate_memory._append_record

            def fail_before_event(path: Path, record: dict[str, object]) -> None:
                if path == store.events_path:
                    raise OSError("injected failure before duplicate event")
                original_append_record(path, record)

            with patch.object(candidate_memory, "_append_record", side_effect=fail_before_event):
                with self.assertRaises(OSError):
                    duplicate_kwargs = {
                        field: original[field]
                        for field in EVENT_FIELDS
                        if field not in {"schema_version", "event_id", "event_type", "event_fingerprint", "created_at"}
                    }
                    store.append_event("DECISION_ACCEPTED", **duplicate_kwargs)

            # Corrupt the durable intent (without adding a second transaction)
            # so selected-tail repair must not accidentally complete it.
            transaction_before = store.transactions_path.read_bytes()
            intent = json.loads(transaction_before.splitlines()[0].decode("utf-8"))
            intent["event"]["claim_or_event"] = "tampered intent"
            transaction_corrupt = canonical_json_bytes(intent) + b"\n"
            store.transactions_path.write_bytes(transaction_corrupt)

            malformed = b'{"schema_version":1}'
            candidate_memory._append_bytes(store.events_path, malformed)
            with self.assertRaises(LedgerCorruptionError):
                store.recover_truncated_tail("events")

            self.assertEqual(store.events_path.read_bytes(), event_prefix + malformed + b"\n")
            self.assertEqual(
                store.relations_path.read_bytes() if store.relations_path.exists() else b"",
                relation_prefix,
            )
            self.assertEqual(store.transactions_path.read_bytes(), transaction_corrupt)
            recovery_path = store.events_path.with_name(store.events_path.name + ".recovery.jsonl")
            recovery = [json.loads(line) for line in recovery_path.read_bytes().splitlines()]
            self.assertEqual(recovery[-1]["action"], "quarantined_truncated_tail")

    def test_wrong_target_pending_intent_is_rejected_before_any_append(self) -> None:
        directory, store = self.make_store()
        with directory:
            original = self.add_event(store, task_id="wrong-target-original")
            wrong_target = self.add_event(store, task_id="wrong-target-other")
            events_before = store.events_path.read_bytes()
            relations_before = store.relations_path.read_bytes() if store.relations_path.exists() else b""

            original_append_record = candidate_memory._append_record

            def fail_before_event(path: Path, record: dict[str, object]) -> None:
                if path == store.events_path:
                    raise OSError("injected failure before duplicate event")
                original_append_record(path, record)

            duplicate_kwargs = {
                field: original[field]
                for field in EVENT_FIELDS
                if field not in {"schema_version", "event_id", "event_type", "event_fingerprint", "created_at"}
            }
            with patch.object(candidate_memory, "_append_record", side_effect=fail_before_event):
                with self.assertRaises(OSError):
                    store.append_event("DECISION_ACCEPTED", **duplicate_kwargs)

            transaction = json.loads(store.transactions_path.read_bytes().splitlines()[0].decode("utf-8"))
            relation = transaction["relation"]
            relation["to_candidate_ids"] = [wrong_target["event_id"]]
            relation["record_id"] = candidate_memory._expected_relation_id(relation, 1)
            transaction["transaction_id"] = candidate_memory._transaction_id(transaction["event"], relation)
            store.transactions_path.write_bytes(canonical_json_bytes(transaction) + b"\n")
            transaction_before_reconcile = store.transactions_path.read_bytes()

            with self.assertRaises(LedgerCorruptionError):
                store.read_events()
            self.assertEqual(store.events_path.read_bytes(), events_before)
            self.assertEqual(
                store.relations_path.read_bytes() if store.relations_path.exists() else b"",
                relations_before,
            )
            self.assertEqual(store.transactions_path.read_bytes(), transaction_before_reconcile)

    def test_orphan_repeated_event_tail_is_quarantined(self) -> None:
        directory, store = self.make_store()
        with directory:
            original = self.add_event(store, task_id="orphan-repeated-event")
            event = dict(original)
            event["event_id"] = candidate_memory._expected_event_id(event, 2)
            fragment = canonical_json_bytes(event)
            prefix = store.events_path.read_bytes()
            candidate_memory._append_bytes(store.events_path, fragment)

            receipt = store.recover_truncated_tail("events")
            self.assertEqual(receipt["action"], "quarantined_truncated_tail")
            self.assertEqual(store.events_path.read_bytes(), prefix + fragment + b"\n")
            self.assertEqual(store.read_events(), [original])

    def test_wrong_target_transaction_backed_duplicate_tails_are_quarantined(self) -> None:
        for ledger in ("events", "relations"):
            with self.subTest(ledger=ledger):
                directory, store = self.make_store()
                with directory:
                    original = self.add_event(store, task_id=f"wrong-tail-original-{ledger}")
                    wrong_target = self.add_event(store, task_id=f"wrong-tail-other-{ledger}")
                    original_append_record = candidate_memory._append_record

                    def fail_at_boundary(path: Path, record: dict[str, object]) -> None:
                        if ledger == "events" and path == store.relations_path:
                            raise OSError("injected failure before duplicate relation")
                        if (
                            ledger == "relations"
                            and path == store.transactions_path
                            and record.get("record_type") == "DUPLICATE_APPEND_COMMIT"
                        ):
                            raise OSError("injected failure before duplicate commit")
                        original_append_record(path, record)

                    duplicate_kwargs = {
                        field: original[field]
                        for field in EVENT_FIELDS
                        if field not in {"schema_version", "event_id", "event_type", "event_fingerprint", "created_at"}
                    }
                    with patch.object(candidate_memory, "_append_record", side_effect=fail_at_boundary):
                        with self.assertRaises(OSError):
                            store.append_event("DECISION_ACCEPTED", **duplicate_kwargs)

                    transaction = json.loads(store.transactions_path.read_bytes().splitlines()[0].decode("utf-8"))
                    relation = transaction["relation"]
                    relation["to_candidate_ids"] = [wrong_target["event_id"]]
                    relation["record_id"] = candidate_memory._expected_relation_id(relation, 1)
                    transaction["transaction_id"] = candidate_memory._transaction_id(transaction["event"], relation)
                    store.transactions_path.write_bytes(canonical_json_bytes(transaction) + b"\n")

                    if ledger == "events":
                        selected_path = store.events_path
                    else:
                        selected_path = store.relations_path
                    selected_before = selected_path.read_bytes()
                    selected_tail = selected_before[:-1]
                    selected_path.write_bytes(selected_tail)
                    with self.assertRaises(LedgerCorruptionError):
                        store.recover_truncated_tail(ledger)

                    self.assertEqual(selected_path.read_bytes(), selected_tail + b"\n")
                    recovery_path = selected_path.with_name(selected_path.name + ".recovery.jsonl")
                    recovery = [json.loads(line) for line in recovery_path.read_bytes().splitlines()]
                    self.assertEqual(recovery[-1]["action"], "quarantined_truncated_tail")
                    self.assertEqual(
                        store.transactions_path.read_bytes().splitlines()[0],
                        canonical_json_bytes(transaction),
                    )
                    self.assertEqual(len(store.transactions_path.read_bytes().splitlines()), 1)

    def test_transaction_journal_tail_recovery_preserves_prior_bytes(self) -> None:
        for tail_kind in ("valid", "malformed"):
            with self.subTest(tail_kind=tail_kind):
                directory, store = self.make_store()
                with directory:
                    original = self.add_event(store, task_id=f"journal-tail-{tail_kind}")
                    before_journal = (
                        store.transactions_path.read_bytes() if store.transactions_path.exists() else b""
                    )
                    if tail_kind == "valid":
                        # A crash after the durable intent write can leave a
                        # complete JSON object without its final delimiter.
                        duplicate_kwargs = {
                            "task_id": original["task_id"],
                            "claim_or_event": original["claim_or_event"],
                        }
                        original_append_record = candidate_memory._append_record

                        def fail_before_duplicate_commit(path: Path, record: dict[str, object]) -> None:
                            if (
                                path == store.transactions_path
                                and record.get("record_type") == "DUPLICATE_APPEND_COMMIT"
                            ):
                                raise OSError("injected failure before duplicate commit")
                            original_append_record(path, record)

                        with patch.object(
                            candidate_memory, "_append_record", side_effect=fail_before_duplicate_commit
                        ):
                            with self.assertRaises(OSError):
                                self.add_event(store, **duplicate_kwargs)
                        journal_with_intent = store.transactions_path.read_bytes()
                        self.assertTrue(journal_with_intent.startswith(before_journal))
                        self.assertTrue(journal_with_intent.endswith(b"\n"))
                        store.transactions_path.write_bytes(journal_with_intent[:-1])
                        prior_tail_bytes = journal_with_intent[:-1]
                        events_before_recovery = store.events_path.read_bytes()
                        relations_before_recovery = (
                            store.relations_path.read_bytes() if store.relations_path.exists() else b""
                        )

                        events = store.read_events()
                        relations = store.read_relations()
                        self.assertEqual(
                            sum(event["event_fingerprint"] == original["event_fingerprint"] for event in events),
                            2,
                        )
                        self.assertEqual(sum(relation["record_type"] == "DUPLICATE_OF" for relation in relations), 1)
                        recovery_path = store.transactions_path.with_name(
                            store.transactions_path.name + ".recovery.jsonl"
                        )
                        recovery = [json.loads(line) for line in recovery_path.read_bytes().splitlines()]
                        self.assertEqual(recovery[-1]["action"], "sealed_missing_newline")
                        self.assertTrue(store.transactions_path.read_bytes().startswith(prior_tail_bytes))
                        self.assertTrue(store.events_path.read_bytes().startswith(events_before_recovery))
                        self.assertTrue(
                            store.relations_path.read_bytes().startswith(relations_before_recovery)
                        )
                    else:
                        malformed_tail = b'{"record_type":"DUPLICATE_APPEND_INTENT"'
                        candidate_memory._append_bytes(store.transactions_path, malformed_tail)
                        prior_tail_bytes = before_journal + malformed_tail
                        events_before_recovery = store.events_path.read_bytes()
                        relations_before_recovery = (
                            store.relations_path.read_bytes() if store.relations_path.exists() else b""
                        )
                        store.read_events()
                        self.assertTrue(store.transactions_path.read_bytes().startswith(prior_tail_bytes))
                        self.assertEqual(store.events_path.read_bytes(), events_before_recovery)
                        self.assertEqual(
                            store.relations_path.read_bytes() if store.relations_path.exists() else b"",
                            relations_before_recovery,
                        )
                        recovery_path = store.transactions_path.with_name(
                            store.transactions_path.name + ".recovery.jsonl"
                        )
                        recovery = [json.loads(line) for line in recovery_path.read_bytes().splitlines()]
                        self.assertEqual(recovery[-1]["action"], "quarantined_truncated_tail")

    def test_noncanonical_and_nondeterministic_persisted_records_fail_closed(self) -> None:
        directory, store = self.make_store()
        with directory:
            event = self.add_event(store, task_id="canonical-record")
            canonical = store.events_path.read_bytes()
            store.events_path.write_bytes(b" " + canonical)
            with self.assertRaises(LedgerCorruptionError):
                store.read_events()
            store.events_path.write_bytes(canonical)
            tampered = json.loads(canonical.decode("utf-8"))
            tampered["event_id"] = "candidate-not-deterministic-0001"
            store.events_path.write_bytes(canonical_json_bytes(tampered) + b"\n")
            with self.assertRaises(LedgerCorruptionError):
                store.read_events()

            store.events_path.write_bytes(canonical)
            other = self.add_event(store, task_id="canonical-relation")
            relation = store.append_relation("SUPPORTS", [event["event_id"]], [other["event_id"]])
            relation_bytes = store.relations_path.read_bytes()
            tampered_relation = json.loads(relation_bytes.decode("utf-8"))
            tampered_relation["record_id"] = "relation-not-deterministic-0001"
            store.relations_path.write_bytes(canonical_json_bytes(tampered_relation) + b"\n")
            with self.assertRaises(LedgerCorruptionError):
                store.read_relations()

    def test_independent_process_appends_are_serialized_by_store_lock(self) -> None:
        directory, store = self.make_store()
        with directory:
            script = """
import sys
from hermes_cli.candidate_memory import CandidateMemoryStore
store = CandidateMemoryStore(sys.argv[1])
prefix = sys.argv[2]
for index in range(8):
    store.append_event(
        "MILESTONE_REACHED",
        project_scope="Yatima",
        claim_or_event=f"{prefix}-{index}",
        task_id=f"{prefix}-{index}",
        created_at="2026-08-28T14:00:00Z",
    )
"""
            processes = [
                subprocess.Popen(
                    [sys.executable, "-c", script, str(store.root), f"process-{index}"],
                    cwd=str(Path(__file__).resolve().parents[2]),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                for index in range(3)
            ]
            results = [process.communicate(timeout=30) for process in processes]
            self.assertTrue(all(process.returncode == 0 for process in processes), results)
            events = store.read_events()
            self.assertEqual(len(events), 24)
            self.assertEqual(len({event["event_id"] for event in events}), 24)


if __name__ == "__main__":
    unittest.main()
