"""Behavior coverage for the bounded protected-board policy seam."""

import json
import sqlite3
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from hermes_cli import fleet_policy as fp
from hermes_cli import kanban_db as kb
import tools.kanban_tools as kt


class _Agent:
    def __init__(self):
        self.steers = []

    def steer(self, text):
        self.steers.append(text)
        return True


def _setup(tmp_path, monkeypatch, board="yatima-portfolio"):
    root = tmp_path / "hermes"
    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(root))
    monkeypatch.setenv("HERMES_KANBAN_BOARD", board)
    monkeypatch.setenv("HERMES_FLEET_POLICY_TEST_OVERRIDES", "1")
    monkeypatch.setenv("HERMES_FLEET_POLICY_DB", str(root / "fleet-policy.db"))
    config = root / "fleet-policy.json"
    monkeypatch.setenv("HERMES_FLEET_POLICY_CONFIG", str(config))
    profile = root / "profiles" / "brain"
    profile.mkdir(parents=True, exist_ok=True)
    (profile / "config.yaml").write_text(
        "model:\n  default: fixture-model\n  provider: fixture-provider\n",
        encoding="utf-8",
    )
    conn = kb.connect(board=board)
    return conn, config


def _task(conn, status="ready"):
    task_id = kb.create_task(
        conn, title="bounded task", body="do the bounded thing", assignee="brain",
    )
    conn.execute("UPDATE tasks SET status = ? WHERE id = ?", (status, task_id))
    conn.commit()
    return task_id


def _config_for(conn, config, task_id, *, approval=False, resources=()):
    envelope = {
        "route": "local-r4",
        "authority": "R3",
        "authority_ceiling": "R3",
        "required_capabilities": ["kanban.read"],
        "mutating": False,
        "exclusive_resources": list(resources),
        "approval_required": approval,
    }
    task = kb.get_task(conn, task_id)
    fingerprint = fp.task_fingerprint(task, kb.list_comments(conn, task_id), envelope)
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps({
        "routes": {
            "local-r4": {
                "admitted": True,
                "capabilities": ["kanban.read"],
                "authority_ceiling": "R4",
                "profile": "brain",
                "model": "fixture-model",
                "provider": "fixture-provider",
            },
        },
        "roles": {"brain": {"authority_ceiling": "R4"}},
        "tasks": {
            task_id: {
                "fingerprint": fingerprint,
                "execution_envelope": envelope,
                "approval_required": approval,
            },
        },
    }), encoding="utf-8")
    return envelope, fingerprint


def test_ordinary_board_claim_is_unchanged_and_needs_no_policy(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(tmp_path / "hermes"))
    monkeypatch.delenv("HERMES_KANBAN_BOARD", raising=False)
    conn = kb.connect()
    task_id = _task(conn)
    claimed = kb.claim_task(conn, task_id, claimer="ordinary")
    assert claimed is not None
    assert conn.execute("SELECT metadata FROM task_runs").fetchone()[0] is None
    assert not (tmp_path / "hermes" / "fleet-policy.db").exists()


def test_protected_claim_fails_closed_without_complete_envelope(tmp_path, monkeypatch):
    conn, _config = _setup(tmp_path, monkeypatch)
    task_id = _task(conn)
    assert kb.claim_task(conn, task_id, claimer="protected") is None
    assert kb.get_task(conn, task_id).status == "ready"
    tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'",
        )
    }
    assert tables == {
        "tasks", "task_links", "task_comments", "task_events", "task_runs",
        "task_attachments", "kanban_notify_subs",
    }


def test_protected_claim_records_receipt_and_trusted_metadata(tmp_path, monkeypatch):
    conn, config = _setup(tmp_path, monkeypatch)
    task_id = _task(conn)
    envelope, fingerprint = _config_for(conn, config, task_id, resources=("gpu",))
    claimed = kb.claim_task(conn, task_id, claimer="protected")
    assert claimed is not None
    run = conn.execute("SELECT id, metadata FROM task_runs").fetchone()
    metadata = json.loads(run["metadata"])
    assert metadata["fleet_policy"]["fingerprint"] == fingerprint
    assert metadata["fleet_policy"]["execution_envelope"] == envelope
    policy_db = sqlite3.connect(str(tmp_path / "hermes" / "fleet-policy.db"))
    try:
        tables = {
            row[0] for row in policy_db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'",
            )
        }
        assert tables == {"task_policy_receipts", "resource_leases", "approvals"}
        assert policy_db.execute("SELECT decision FROM task_policy_receipts").fetchone()[0] == "allowed"
        assert policy_db.execute("SELECT resource FROM resource_leases").fetchone()[0] == "gpu"
    finally:
        policy_db.close()


def test_review_claim_uses_the_same_protected_hook(tmp_path, monkeypatch):
    conn, config = _setup(tmp_path, monkeypatch)
    task_id = _task(conn, status="review")
    _config_for(conn, config, task_id)
    assert kb.claim_review_task(conn, task_id, claimer="reviewer") is not None
    event = conn.execute("SELECT payload FROM task_events WHERE kind = 'claimed'").fetchone()
    assert json.loads(event[0])["source_status"] == "review"


def test_worker_fingerprint_mismatch_releases_run_before_agent(tmp_path, monkeypatch):
    conn, config = _setup(tmp_path, monkeypatch)
    task_id = _task(conn)
    _config_for(conn, config, task_id, resources=("gpu",))
    assert kb.claim_task(conn, task_id, claimer="protected") is not None
    run = conn.execute("SELECT id, metadata FROM task_runs").fetchone()
    metadata = json.loads(run["metadata"])
    metadata["fleet_policy"]["fingerprint"] = "0" * 64
    assert not fp.worker_start_recheck(conn, task_id, run["id"], metadata)
    task = kb.get_task(conn, task_id)
    assert task.status == "ready"
    outcome = conn.execute("SELECT outcome FROM task_runs WHERE id = ?", (run["id"],)).fetchone()[0]
    assert outcome == "NEEDS_REVALIDATION"
    policy_db = sqlite3.connect(str(tmp_path / "hermes" / "fleet-policy.db"))
    try:
        assert policy_db.execute("SELECT decision FROM task_policy_receipts").fetchone()[0] == "cancelled"
        assert policy_db.execute("SELECT COUNT(*) FROM resource_leases").fetchone()[0] == 0
    finally:
        policy_db.close()


def _bind_workspace(conn, task_id, run_id, board, path, branch=""):
    path.mkdir(parents=True, exist_ok=True)
    kb.set_workspace_path(conn, task_id, path)
    assert fp.refresh_workspace_identity(conn, task_id, run_id, board, str(path), branch)


@pytest.mark.parametrize("source_status", ["ready", "review"])
def test_worker_fingerprint_matches_for_both_native_lanes(tmp_path, monkeypatch, source_status):
    conn, config = _setup(tmp_path, monkeypatch)
    task_id = _task(conn, status=source_status)
    _config_for(conn, config, task_id)
    claimed = (
        kb.claim_review_task(conn, task_id, claimer="reviewer")
        if source_status == "review"
        else kb.claim_task(conn, task_id, claimer="worker")
    )
    assert claimed is not None
    run = conn.execute("SELECT id, metadata FROM task_runs").fetchone()
    _bind_workspace(conn, task_id, run["id"], "yatima-portfolio", tmp_path / source_status)
    run = conn.execute("SELECT id, metadata FROM task_runs").fetchone()
    assert fp.worker_start_recheck(conn, task_id, run["id"], json.loads(run["metadata"])) is True


def test_worker_lane_metadata_tamper_releases_to_event_lane(tmp_path, monkeypatch):
    conn, config = _setup(tmp_path, monkeypatch)
    task_id = _task(conn, status="review")
    _config_for(conn, config, task_id, resources=("gpu",))
    assert kb.claim_review_task(conn, task_id, claimer="reviewer") is not None
    run = conn.execute("SELECT id, metadata FROM task_runs").fetchone()
    metadata = json.loads(run["metadata"])
    metadata["fleet_policy"]["source_status"] = "ready"
    assert fp.worker_start_recheck(conn, task_id, run["id"], metadata) is False
    assert kb.get_task(conn, task_id).status == "review"


def test_late_old_worker_fails_after_reclaim_and_new_claim(tmp_path, monkeypatch):
    conn, config = _setup(tmp_path, monkeypatch)
    task_id = _task(conn)
    _config_for(conn, config, task_id, resources=("gpu",))
    assert kb.claim_task(conn, task_id, claimer="old-worker") is not None
    old_run = conn.execute("SELECT id, metadata FROM task_runs ORDER BY id").fetchone()
    _bind_workspace(conn, task_id, old_run["id"], "yatima-portfolio", tmp_path / "old-worker")
    old_metadata = json.loads(conn.execute("SELECT metadata FROM task_runs WHERE id = ?", (old_run["id"],)).fetchone()[0])
    assert kb.reclaim_task(conn, task_id, reason="stale-old-worker") is True
    assert kb.claim_task(conn, task_id, claimer="new-worker") is not None
    new_run = conn.execute("SELECT id FROM task_runs WHERE status = 'running'").fetchone()[0]
    assert new_run != old_run["id"]
    assert fp.worker_start_guard(task_id, old_run["id"], board="yatima-portfolio") is False
    assert fp.worker_start_recheck(conn, task_id, old_run["id"], old_metadata, board="yatima-portfolio") is False
    # The late worker cannot release the fresh claim it does not own.
    assert kb.get_task(conn, task_id).status == "running"
    assert conn.execute("SELECT status FROM task_runs WHERE id = ?", (new_run,)).fetchone()[0] == "running"


def test_missing_protected_metadata_releases_active_review_run(tmp_path, monkeypatch):
    conn, config = _setup(tmp_path, monkeypatch)
    task_id = _task(conn, status="review")
    _config_for(conn, config, task_id, resources=("gpu",))
    assert kb.claim_review_task(conn, task_id, claimer="reviewer") is not None
    run_id = conn.execute("SELECT id FROM task_runs").fetchone()[0]
    assert fp.worker_start_recheck(conn, task_id, None, None) is False
    assert kb.get_task(conn, task_id).status == "review"
    assert conn.execute("SELECT outcome FROM task_runs WHERE id = ?", (run_id,)).fetchone()[0] == "NEEDS_REVALIDATION"
    policy_db = sqlite3.connect(str(tmp_path / "hermes" / "fleet-policy.db"))
    try:
        assert policy_db.execute("SELECT decision FROM task_policy_receipts").fetchone()[0] == "cancelled"
        assert policy_db.execute("SELECT COUNT(*) FROM resource_leases").fetchone()[0] == 0
    finally:
        policy_db.close()


def test_explicit_dispatch_board_cannot_fall_back_to_default(tmp_path, monkeypatch):
    conn, config = _setup(tmp_path, monkeypatch)
    task_id = _task(conn)
    _config_for(conn, config, task_id)
    monkeypatch.setenv("HERMES_KANBAN_BOARD", "default")
    assert kb.claim_task(conn, task_id, board="yatima-portfolio", claimer="explicit") is not None


@pytest.mark.parametrize("source_status", ["ready", "review"])
def test_protected_connection_identity_guards_direct_claim_with_default_current_board(
    tmp_path, monkeypatch, source_status,
):
    conn, config = _setup(tmp_path, monkeypatch)
    task_id = _task(conn, status=source_status)
    # No policy file is a protected denial even though the caller omits board.
    assert (
        kb.claim_review_task(conn, task_id, claimer="missing-policy")
        if source_status == "review"
        else kb.claim_task(conn, task_id, claimer="missing-policy")
    ) is None
    _config_for(conn, config, task_id)
    monkeypatch.setenv("HERMES_KANBAN_BOARD", "default")
    claimed = (
        kb.claim_review_task(conn, task_id, claimer="direct-review")
        if source_status == "review"
        else kb.claim_task(conn, task_id, claimer="direct-ready")
    )
    assert claimed is not None
    policy_db = sqlite3.connect(str(tmp_path / "hermes" / "fleet-policy.db"))
    try:
        assert policy_db.execute(
            "SELECT decision FROM task_policy_receipts WHERE decision = 'allowed'"
        ).fetchone()[0] == "allowed"
    finally:
        policy_db.close()


def test_explicit_protected_label_cannot_target_ordinary_connection(tmp_path, monkeypatch):
    conn, config = _setup(tmp_path, monkeypatch, board="default")
    task_id = _task(conn)
    _config_for(conn, config, task_id)
    assert kb.claim_task(conn, task_id, board="yatima-portfolio", claimer="wrong-db") is None
    assert kb.get_task(conn, task_id).status == "ready"


def test_reconciliation_is_scoped_to_board_and_exact_task_run(tmp_path, monkeypatch):
    portfolio_conn, config = _setup(tmp_path, monkeypatch, board="yatima-portfolio")
    portfolio_task = _task(portfolio_conn)
    portfolio_envelope, _ = _config_for(
        portfolio_conn, config, portfolio_task, resources=("gpu-portfolio",)
    )
    canary_conn = kb.connect(board="yatima-canary")
    canary_task = _task(canary_conn)
    canary_obj = kb.get_task(canary_conn, canary_task)
    canary_envelope = {**portfolio_envelope, "exclusive_resources": ["gpu-canary"]}
    policy = json.loads(config.read_text(encoding="utf-8"))
    policy["tasks"][canary_task] = {
        "fingerprint": fp.task_fingerprint(canary_obj, kb.list_comments(canary_conn, canary_task), canary_envelope),
        "execution_envelope": canary_envelope,
    }
    config.write_text(json.dumps(policy), encoding="utf-8")
    assert kb.claim_task(portfolio_conn, portfolio_task, board="yatima-portfolio", claimer="portfolio") is not None
    assert kb.claim_task(canary_conn, canary_task, board="yatima-canary", claimer="canary") is not None
    db = sqlite3.connect(str(tmp_path / "hermes" / "fleet-policy.db"))
    try:
        rows = {
            row[0]: (row[1], row[2])
            for row in db.execute(
                "SELECT task_id, run_id, decision FROM task_policy_receipts WHERE task_id IN (?, ?)",
                (portfolio_task, canary_task),
            )
        }
        assert rows[portfolio_task][0] == rows[canary_task][0]  # colliding native run ids
        assert rows[portfolio_task][1] == rows[canary_task][1] == "allowed"
        assert db.execute(
            "SELECT task_id FROM resource_leases WHERE resource = 'gpu-portfolio'",
        ).fetchone()[0] == portfolio_task
    finally:
        db.close()


def test_cas_loss_cancels_only_the_losing_receipt(tmp_path, monkeypatch):
    conn, config = _setup(tmp_path, monkeypatch)
    task_id = _task(conn)
    _config_for(conn, config, task_id, resources=("gpu",))
    assert kb.claim_task(conn, task_id, claimer="winner") is not None
    # The second supported claim reaches policy, then loses the native CAS.
    assert kb.claim_task(conn, task_id, claimer="loser") is None
    db = sqlite3.connect(str(tmp_path / "hermes" / "fleet-policy.db"))
    try:
        rows = db.execute("SELECT decision, receipt_id FROM task_policy_receipts ORDER BY created_at").fetchall()
        assert rows[-1][0] == "cancelled"
        assert db.execute("SELECT COUNT(*) FROM resource_leases").fetchone()[0] == 1
        assert db.execute("SELECT receipt_id FROM resource_leases").fetchone()[0] == rows[0][1]
    finally:
        db.close()


def test_two_tasks_racing_for_one_exclusive_lease_have_one_winner(tmp_path, monkeypatch):
    conn, config = _setup(tmp_path, monkeypatch)
    first = _task(conn)
    second = _task(conn)
    _config_for(conn, config, first, resources=("gpu",))
    policy = json.loads(config.read_text(encoding="utf-8"))
    envelope = policy["tasks"][first]["execution_envelope"]
    task = kb.get_task(conn, second)
    second_fp = fp.task_fingerprint(task, kb.list_comments(conn, second), envelope)
    policy["tasks"][second] = {
        "fingerprint": second_fp,
        "execution_envelope": envelope,
    }
    config.write_text(json.dumps(policy), encoding="utf-8")
    barrier = threading.Barrier(2)

    def claim(task_id):
        local = kb.connect(board="yatima-portfolio")
        try:
            barrier.wait()
            return fp.authorize_claim(
                local, task_id, board="yatima-portfolio", now=100,
            ).allowed
        finally:
            local.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, (first, second)))
    assert sum(results) == 1


def test_receipt_binding_failure_compensates_and_rolls_back(tmp_path, monkeypatch):
    conn, config = _setup(tmp_path, monkeypatch)
    task_id = _task(conn)
    _config_for(conn, config, task_id, resources=("gpu",))
    monkeypatch.setattr(fp, "bind_receipt_run", lambda *_args: False)
    with pytest.raises(RuntimeError, match="receipt binding"):
        kb.claim_task(conn, task_id, claimer="binding-failure")
    assert kb.get_task(conn, task_id).status == "ready"
    db = sqlite3.connect(str(tmp_path / "hermes" / "fleet-policy.db"))
    try:
        assert db.execute("SELECT decision FROM task_policy_receipts").fetchone()[0] == "cancelled"
        assert db.execute("SELECT COUNT(*) FROM resource_leases").fetchone()[0] == 0
    finally:
        db.close()


def test_claim_transaction_exception_cancels_receipt_and_lease(tmp_path, monkeypatch):
    conn, config = _setup(tmp_path, monkeypatch)
    task_id = _task(conn)
    _config_for(conn, config, task_id, resources=("gpu",))
    original_append = kb._append_event
    monkeypatch.setattr(kb, "_append_event", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("event failure")))
    with pytest.raises(RuntimeError, match="event failure"):
        kb.claim_task(conn, task_id, claimer="event-failure")
    monkeypatch.setattr(kb, "_append_event", original_append)
    assert kb.get_task(conn, task_id).status == "ready"
    db = sqlite3.connect(str(tmp_path / "hermes" / "fleet-policy.db"))
    try:
        assert db.execute("SELECT decision FROM task_policy_receipts").fetchone()[0] == "cancelled"
        assert db.execute("SELECT COUNT(*) FROM resource_leases").fetchone()[0] == 0
    finally:
        db.close()


def test_claim_commit_exception_compensates_receipt_and_lease(tmp_path, monkeypatch):
    conn, config = _setup(tmp_path, monkeypatch)
    task_id = _task(conn)
    _config_for(conn, config, task_id, resources=("gpu",))
    original_boundary = kb._execute_boundary_with_retry

    def fail_commit(connection, sql):
        if sql == "COMMIT":
            raise RuntimeError("commit failure")
        return original_boundary(connection, sql)

    monkeypatch.setattr(kb, "_execute_boundary_with_retry", fail_commit)
    with pytest.raises(RuntimeError, match="commit failure"):
        kb.claim_task(conn, task_id, claimer="commit-failure")
    assert kb.get_task(conn, task_id).status == "ready"
    db = sqlite3.connect(str(tmp_path / "hermes" / "fleet-policy.db"))
    try:
        assert db.execute("SELECT decision FROM task_policy_receipts").fetchone()[0] == "cancelled"
        assert db.execute("SELECT COUNT(*) FROM resource_leases").fetchone()[0] == 0
    finally:
        db.close()


def test_bound_receipt_ttl_reconciles_after_native_run_disappears(tmp_path, monkeypatch):
    conn, config = _setup(tmp_path, monkeypatch)
    task_id = _task(conn)
    _config_for(conn, config, task_id, resources=("gpu",))
    assert kb.claim_task(conn, task_id, claimer="crash-simulated") is not None
    conn.execute("UPDATE tasks SET status = 'ready', current_run_id = NULL WHERE id = ?", (task_id,))
    conn.execute("DELETE FROM task_runs WHERE task_id = ?", (task_id,))
    conn.commit()
    fp.authorize_claim(conn, "absent-task", board="yatima-portfolio", now=int(time.time()) + 7200)
    db = sqlite3.connect(str(tmp_path / "hermes" / "fleet-policy.db"))
    try:
        assert db.execute("SELECT decision FROM task_policy_receipts WHERE task_id = ?", (task_id,)).fetchone()[0] == "cancelled"
        assert db.execute("SELECT COUNT(*) FROM resource_leases").fetchone()[0] == 0
    finally:
        db.close()


def test_comment_watermark_stores_but_defers_mutating_steer(tmp_path, monkeypatch):
    conn, _config = _setup(tmp_path, monkeypatch)
    task_id = _task(conn)
    conn.close()
    monkeypatch.setenv("HERMES_KANBAN_TASK", task_id)
    monkeypatch.setenv("HERMES_PROFILE", "brain")
    monkeypatch.setenv("HERMES_KANBAN_MUTATING_RUN", "1")
    kt._comment_watermark.clear()
    kt._comment_poll_last_attempt = 0.0
    agent = _Agent()
    assert kt.inject_new_comments_from_env(agent) is False
    conn = kb.connect(board="yatima-portfolio")
    kb.add_comment(conn, task_id, author="operator", body="defer this note")
    conn.close()
    kt._comment_poll_last_attempt = 0.0
    assert kt.inject_new_comments_from_env(agent) is False
    assert kt._comment_watermark[task_id] > 0
    monkeypatch.delenv("HERMES_KANBAN_MUTATING_RUN")
    conn = kb.connect(board="yatima-portfolio")
    kb.add_comment(conn, task_id, author="operator", body="live read-only note")
    conn.close()
    kt._comment_poll_last_attempt = 0.0
    assert kt.inject_new_comments_from_env(agent) is True
    assert "live read-only note" in agent.steers[-1]


def test_mutating_comment_steering_is_deferred_but_read_only_is_live(monkeypatch):
    payload = {"fleet_policy": {"mutating": True}}
    monkeypatch.setenv("HERMES_KANBAN_POLICY_METADATA", json.dumps(payload))
    assert fp.live_comment_steering_allowed() is False
    monkeypatch.setenv("HERMES_KANBAN_POLICY_METADATA", json.dumps({"fleet_policy": {"mutating": False}}))
    assert fp.live_comment_steering_allowed() is True


def test_dispatch_resolves_and_rechecks_scratch_and_worktree_identity(tmp_path, monkeypatch):
    conn, config = _setup(tmp_path, monkeypatch)
    scratch_id = _task(conn)
    worktree_id = _task(conn)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    (repo / "README.md").write_text("fixture\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture"],
        check=True,
    )
    conn.execute("UPDATE tasks SET workspace_kind = 'worktree', workspace_path = ?, branch_name = ? WHERE id = ?", (str(repo), "fleet-branch", worktree_id))
    conn.commit()
    envelope = {
        "route": "local-r4", "authority": "R3", "authority_ceiling": "R3",
        "required_capabilities": ["kanban.read"], "mutating": False,
    }
    policy = {
        "routes": {"local-r4": {
            "admitted": True, "capabilities": ["kanban.read"], "authority_ceiling": "R4",
            "profile": "brain", "model": "fixture-model", "provider": "fixture-provider",
        }},
        "roles": {"brain": {"authority_ceiling": "R4"}}, "tasks": {},
    }
    for task_id in (scratch_id, worktree_id):
        task = kb.get_task(conn, task_id)
        policy["tasks"][task_id] = {
            "fingerprint": fp.task_fingerprint(task, kb.list_comments(conn, task_id), envelope),
            "execution_envelope": envelope,
        }
    config.write_text(json.dumps(policy), encoding="utf-8")
    from hermes_cli import profiles
    monkeypatch.setattr(profiles, "profile_exists", lambda _profile: True)
    rechecked = []

    def spawn(task, workspace, board=None):
        row = conn.execute("SELECT id, metadata FROM task_runs WHERE task_id = ?", (task.id,)).fetchone()
        metadata = json.loads(row["metadata"])
        rechecked.append((task.id, Path(workspace)))
        assert fp.worker_start_recheck(conn, task.id, row["id"], metadata, board=board)
        return None

    result = kb.dispatch_once(conn, board="yatima-portfolio", spawn_fn=spawn, max_spawn=2)
    assert {item[0] for item in rechecked} == {scratch_id, worktree_id}
    assert result.spawned
    for task_id in (scratch_id, worktree_id):
        task = kb.get_task(conn, task_id)
        run = conn.execute("SELECT metadata FROM task_runs WHERE task_id = ?", (task_id,)).fetchone()
        identity = json.loads(run["metadata"])["fleet_policy"]["workspace_identity"]
        assert identity["path"] == str(Path(task.workspace_path).resolve())
        assert identity["branch"] == (task.branch_name or "")


def test_relative_config_pause_path_is_anchored_to_shared_root(tmp_path, monkeypatch):
    conn, config = _setup(tmp_path, monkeypatch)
    task_id = _task(conn)
    _config_for(conn, config, task_id)
    pause = tmp_path / "hermes" / "fleet.pause"
    pause.touch()
    monkeypatch.setattr(fp, "_config_policy_settings", lambda: {"pause_file": "fleet.pause"})
    assert kb.claim_task(conn, task_id, claimer="relative-pause") is None


@pytest.mark.parametrize("field,value", [
    ("assignee", "other-profile"),
    ("model_override", "other-model"),
    ("provider_override", "other-provider"),
])
def test_route_identity_mismatch_is_denied(tmp_path, monkeypatch, field, value):
    conn, config = _setup(tmp_path, monkeypatch)
    task_id = _task(conn)
    if field == "assignee":
        conn.execute("UPDATE tasks SET assignee = ? WHERE id = ?", (value, task_id))
    elif field == "model_override":
        conn.execute("UPDATE tasks SET model_override = ?, provider_override = NULL WHERE id = ?", (value, task_id))
    else:
        conn.execute("UPDATE tasks SET model_override = 'fixture-model', provider_override = ? WHERE id = ?", (value, task_id))
    conn.commit()
    task = kb.get_task(conn, task_id)
    envelope, _ = _config_for(conn, config, task_id)
    policy = json.loads(config.read_text(encoding="utf-8"))
    policy["tasks"][task_id]["fingerprint"] = fp.task_fingerprint(task, kb.list_comments(conn, task_id), envelope)
    config.write_text(json.dumps(policy), encoding="utf-8")
    assert kb.claim_task(conn, task_id, claimer="identity-mismatch") is None


def test_provider_only_override_is_denied_like_default_spawn(tmp_path, monkeypatch):
    conn, config = _setup(tmp_path, monkeypatch)
    task_id = _task(conn)
    conn.execute(
        "UPDATE tasks SET provider_override = ?, model_override = NULL WHERE id = ?",
        ("other-provider", task_id),
    )
    conn.commit()
    task = kb.get_task(conn, task_id)
    envelope, _ = _config_for(conn, config, task_id)
    policy = json.loads(config.read_text(encoding="utf-8"))
    policy["tasks"][task_id]["fingerprint"] = fp.task_fingerprint(task, kb.list_comments(conn, task_id), envelope)
    config.write_text(json.dumps(policy), encoding="utf-8")
    assert kb.claim_task(conn, task_id, claimer="provider-only") is None


def test_route_identity_is_required_even_when_route_is_admitted(tmp_path, monkeypatch):
    conn, config = _setup(tmp_path, monkeypatch)
    task_id = _task(conn)
    _config_for(conn, config, task_id)
    policy = json.loads(config.read_text(encoding="utf-8"))
    for key in ("profile", "model", "provider"):
        policy["routes"]["local-r4"].pop(key)
    config.write_text(json.dumps(policy), encoding="utf-8")
    assert kb.claim_task(conn, task_id, claimer="identity-missing") is None


def test_policy_role_cannot_substitute_for_actual_assignee(tmp_path, monkeypatch):
    conn, config = _setup(tmp_path, monkeypatch)
    task_id = _task(conn)
    _config_for(conn, config, task_id)
    policy = json.loads(config.read_text(encoding="utf-8"))
    policy["tasks"][task_id]["role"] = "higher-ceiling-role"
    config.write_text(json.dumps(policy), encoding="utf-8")
    assert kb.claim_task(conn, task_id, claimer="role-substitution") is None


def test_production_config_yaml_and_profile_defaults_authorize_without_test_overrides(tmp_path, monkeypatch):
    conn, config = _setup(tmp_path, monkeypatch)
    task_id = _task(conn)
    _config_for(conn, config, task_id)
    fleet_settings = json.loads(config.read_text(encoding="utf-8"))
    (tmp_path / "hermes" / "config.yaml").write_text(
        json.dumps({"fleet_policy": {**fleet_settings, "database": "kanban/fleet-policy.db"}}),
        encoding="utf-8",
    )
    monkeypatch.delenv("HERMES_FLEET_POLICY_TEST_OVERRIDES")
    monkeypatch.delenv("HERMES_FLEET_POLICY_CONFIG")
    monkeypatch.delenv("HERMES_FLEET_POLICY_DB")
    assert kb.claim_task(conn, task_id, claimer="production-config") is not None
    assert (tmp_path / "hermes" / "kanban" / "fleet-policy.db").is_file()


def test_profile_identity_mutation_after_claim_needs_revalidation(tmp_path, monkeypatch):
    conn, config = _setup(tmp_path, monkeypatch)
    task_id = _task(conn)
    _config_for(conn, config, task_id)
    assert kb.claim_task(conn, task_id, claimer="identity-frozen") is not None
    run = conn.execute("SELECT id FROM task_runs").fetchone()
    profile_config = tmp_path / "hermes" / "profiles" / "brain" / "config.yaml"
    profile_config.write_text(
        "model:\n  default: changed-model\n  provider: changed-provider\n",
        encoding="utf-8",
    )
    _bind_workspace(conn, task_id, run["id"], "yatima-portfolio", tmp_path / "mutated-profile")
    metadata = json.loads(conn.execute("SELECT metadata FROM task_runs WHERE id = ?", (run["id"],)).fetchone()[0])
    assert fp.worker_start_recheck(conn, task_id, run["id"], metadata) is False
    assert kb.get_task(conn, task_id).status == "ready"
    db = sqlite3.connect(str(tmp_path / "hermes" / "fleet-policy.db"))
    try:
        assert db.execute("SELECT decision FROM task_policy_receipts").fetchone()[0] == "cancelled"
    finally:
        db.close()


def test_cli_worker_guard_exits_before_hermes_cli_construction(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(tmp_path / "hermes"))
    monkeypatch.setenv("HERMES_KANBAN_BOARD", "yatima-portfolio")
    monkeypatch.setenv("HERMES_KANBAN_TASK", "missing-task")
    monkeypatch.setenv("HERMES_KANBAN_RUN_ID", "1")
    constructed = []
    allowed = fp.worker_start_guard("missing-task", 1, board="yatima-portfolio")
    if allowed:
        constructed.append("HermesCLI")
    assert allowed is False
    assert constructed == []


def test_ordinary_write_exception_remains_native(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(tmp_path / "hermes"))
    conn = kb.connect()
    original_append = kb._append_event
    monkeypatch.setattr(kb, "_append_event", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("ordinary event failure")))
    with pytest.raises(RuntimeError, match="ordinary event failure"):
        kb.create_task(conn, title="ordinary", body="write")
    monkeypatch.setattr(kb, "_append_event", original_append)
    assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0


@pytest.mark.parametrize("case", [
    "pause", "route", "capability", "authority", "approval", "lease", "manual", "candidate",
])
def test_policy_failures_are_independently_fail_closed(tmp_path, monkeypatch, case):
    conn, config = _setup(tmp_path, monkeypatch)
    task_id = _task(conn)
    _config_for(conn, config, task_id, approval=(case == "approval"), resources=("gpu",) if case == "lease" else ())
    policy = json.loads(config.read_text(encoding="utf-8"))
    if case == "pause":
        pause = tmp_path / "pause.flag"
        pause.touch()
        monkeypatch.setenv("HERMES_FLEET_POLICY_TEST_PAUSE_FILE", str(pause))
    elif case == "route":
        policy["routes"]["local-r4"]["admitted"] = False
        config.write_text(json.dumps(policy), encoding="utf-8")
    elif case == "capability":
        policy["routes"]["local-r4"]["capabilities"] = []
        config.write_text(json.dumps(policy), encoding="utf-8")
    elif case == "authority":
        policy["routes"]["local-r4"]["authority_ceiling"] = "R2"
        config.write_text(json.dumps(policy), encoding="utf-8")
    elif case == "lease":
        # Create the policy schema once, then reserve the resource for another
        # task before the real claim.
        fp.authorize_claim(conn, "other-task", board="yatima-portfolio", now=1)
        db = sqlite3.connect(str(tmp_path / "hermes" / "fleet-policy.db"))
        db.execute(
            "INSERT INTO resource_leases "
            "(resource, lease_id, task_id, receipt_id, fingerprint, acquired_at, expires_at) "
            "VALUES ('gpu', 'lease', 'other-task', 'receipt', 'fingerprint', 1, 4102444800)",
        )
        db.commit()
        db.close()
    elif case == "manual":
        policy["routes"]["local-r4"]["manual_only"] = True
        config.write_text(json.dumps(policy), encoding="utf-8")
    elif case == "candidate":
        policy["routes"]["local-r4"]["candidate_only"] = True
        config.write_text(json.dumps(policy), encoding="utf-8")
    assert kb.claim_task(conn, task_id, claimer="failure") is None
    db = sqlite3.connect(str(tmp_path / "hermes" / "fleet-policy.db"))
    try:
        assert db.execute(
            "SELECT decision FROM task_policy_receipts ORDER BY created_at DESC LIMIT 1",
        ).fetchone()[0] == "denied"
    finally:
        db.close()


def test_expired_approval_is_denied(tmp_path, monkeypatch):
    conn, config = _setup(tmp_path, monkeypatch)
    task_id = _task(conn)
    _envelope, fingerprint = _config_for(conn, config, task_id, approval=True)
    fp.authorize_claim(conn, "missing-task", board="yatima-portfolio", now=1)
    db = sqlite3.connect(str(tmp_path / "hermes" / "fleet-policy.db"))
    db.execute(
        "INSERT INTO approvals (approval_id, task_id, fingerprint, authority, status, created_at, expires_at) "
        "VALUES ('expired', ?, ?, 'R3', 'approved', 1, 1)",
        (task_id, fingerprint),
    )
    db.commit()
    db.close()
    assert kb.claim_task(conn, task_id, claimer="expired-approval") is None


def test_missing_authority_ceiling_is_not_treated_as_checked(tmp_path, monkeypatch):
    conn, config = _setup(tmp_path, monkeypatch)
    task_id = _task(conn)
    _config_for(conn, config, task_id)
    policy = json.loads(config.read_text(encoding="utf-8"))
    del policy["roles"]["brain"]["authority_ceiling"]
    config.write_text(json.dumps(policy), encoding="utf-8")
    assert kb.claim_task(conn, task_id, claimer="missing-ceiling") is None


def test_protected_policy_db_outage_fails_closed_but_ordinary_does_not_break(tmp_path, monkeypatch):
    conn, config = _setup(tmp_path, monkeypatch)
    task_id = _task(conn)
    _config_for(conn, config, task_id)
    monkeypatch.setattr(fp, "_open_policy_db", lambda: (_ for _ in ()).throw(OSError("offline")))
    assert kb.claim_task(conn, task_id, claimer="outage") is None
    monkeypatch.setenv("HERMES_KANBAN_BOARD", "default")
    ordinary_conn = kb.connect()
    ordinary_id = _task(ordinary_conn)
    assert kb.claim_task(ordinary_conn, ordinary_id, claimer="ordinary") is not None
