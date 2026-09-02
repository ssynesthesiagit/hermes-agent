"""Focused tests for task-scoped ``kanban dispatch --task``."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import pytest

from hermes_cli import kanban as cli
from hermes_cli import kanban_db as kb


@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    monkeypatch.setattr(kb, "_memory_pressure_level", lambda: "unknown")
    return home


def _spawn_recorder(spawned):
    def spawn(task, workspace, board=None):
        spawned.append(task.id)
        return 4242

    return spawn


def test_target_dispatch_spawns_only_selected_ready_task_and_skips_review(
    kanban_home, monkeypatch,
):
    monkeypatch.setattr("hermes_cli.profiles.profile_exists", lambda _name: True)
    spawned = []
    with kb.connect() as conn:
        target = kb.create_task(conn, title="selected", assignee="alice")
        other = kb.create_task(conn, title="other", assignee="alice")
        review = kb.create_task(conn, title="review", assignee="alice")
        conn.execute("UPDATE tasks SET status = 'review' WHERE id = ?", (review,))

        result = kb.dispatch_once(
            conn,
            spawn_fn=_spawn_recorder(spawned),
            target_task_id=target,
            max_spawn=1,
            reconcile_orphans=False,
        )

        assert [row[0] for row in result.spawned] == [target]
        assert spawned == [target]
        assert kb.get_task(conn, other).status == "ready"
        assert kb.get_task(conn, review).status == "review"


@pytest.mark.parametrize("status", ["running", "review", "done"])
def test_target_dispatch_never_falls_back_for_not_ready_target(
    kanban_home, monkeypatch, status,
):
    monkeypatch.setattr("hermes_cli.profiles.profile_exists", lambda _name: True)
    spawned = []
    with kb.connect() as conn:
        target = kb.create_task(conn, title="not-ready", assignee="alice")
        other = kb.create_task(conn, title="fallback", assignee="alice")
        conn.execute("UPDATE tasks SET status = ? WHERE id = ?", (status, target))
        result = kb.dispatch_once(
            conn,
            spawn_fn=_spawn_recorder(spawned),
            target_task_id=target,
            reconcile_orphans=False,
        )

    assert result.spawned == []
    assert spawned == []
    with kb.connect() as conn:
        assert kb.get_task(conn, other).status == "ready"


def test_target_dispatch_missing_or_wrong_board_never_spawns(kanban_home, monkeypatch):
    monkeypatch.setattr("hermes_cli.profiles.profile_exists", lambda _name: True)
    kb.create_board("other")
    spawned = []
    with kb.connect(board="other") as other_conn:
        other_id = kb.create_task(other_conn, title="other board", assignee="alice")
    with kb.connect() as conn:
        fallback = kb.create_task(conn, title="default board", assignee="alice")
        for target in ("t_missing", other_id):
            result = kb.dispatch_once(
                conn,
                spawn_fn=_spawn_recorder(spawned),
                target_task_id=target,
                reconcile_orphans=False,
            )
            assert result.spawned == []
        assert kb.get_task(conn, fallback).status == "ready"
    assert spawned == []


def test_target_dispatch_keeps_max_one_and_board_wide_default_compatibility(
    kanban_home, monkeypatch,
):
    monkeypatch.setattr("hermes_cli.profiles.profile_exists", lambda _name: True)
    selected_spawns = []
    with kb.connect() as conn:
        first = kb.create_task(conn, title="first", assignee="alice")
        second = kb.create_task(conn, title="second", assignee="alice")
        result = kb.dispatch_once(
            conn,
            spawn_fn=_spawn_recorder(selected_spawns),
            target_task_id=second,
            max_spawn=1,
            reconcile_orphans=False,
        )
        assert selected_spawns == [second]
        assert len(result.spawned) == 1

    default_spawns = []
    with kb.connect() as conn:
        third = kb.create_task(conn, title="third", assignee="alice")
        result = kb.dispatch_once(
            conn,
            spawn_fn=_spawn_recorder(default_spawns),
            reconcile_orphans=False,
        )
    assert first in default_spawns
    assert result.spawned


def test_cli_dispatch_parser_and_handler_pass_target(monkeypatch, capsys):
    parser = argparse.ArgumentParser(prog="hermes")
    sub = parser.add_subparsers(dest="command")
    cli.build_parser(sub)
    args = parser.parse_args([
        "kanban", "dispatch", "--task", "t_deadbeef", "--max-in-progress", "1", "--json",
    ])
    captured = {}

    def fake_dispatch(_conn, **kwargs):
        captured.update(kwargs)
        return kb.DispatchResult()

    monkeypatch.setattr(cli.kb, "dispatch_once", fake_dispatch)
    monkeypatch.setattr(cli.kb, "connect_closing", lambda: _closed_context())
    cli._cmd_dispatch(args)
    assert captured["target_task_id"] == "t_deadbeef"
    assert captured["max_in_progress"] == 1
    assert json.loads(capsys.readouterr().out)["spawned"] == []


def test_cli_max_in_progress_overrides_config(monkeypatch, capsys):
    parser = argparse.ArgumentParser(prog="hermes")
    sub = parser.add_subparsers(dest="command")
    cli.build_parser(sub)
    args = parser.parse_args(["kanban", "dispatch", "--max-in-progress", "1", "--json"])
    monkeypatch.setattr(
        "hermes_cli.config.load_config",
        lambda: {"kanban": {"max_in_progress": 9}},
    )
    captured = {}

    def fake_dispatch(_conn, **kwargs):
        captured.update(kwargs)
        return kb.DispatchResult()

    monkeypatch.setattr(cli.kb, "dispatch_once", fake_dispatch)
    monkeypatch.setattr(cli.kb, "connect_closing", lambda: _closed_context())
    cli._cmd_dispatch(args)
    assert captured["max_in_progress"] == 1
    assert json.loads(capsys.readouterr().out)["spawned"] == []


def test_two_exact_target_dispatches_respect_global_max_in_progress_one(
    kanban_home, monkeypatch,
):
    monkeypatch.setattr("hermes_cli.profiles.profile_exists", lambda _name: True)
    spawned = []

    def spawn(task, workspace, board=None):
        spawned.append(task.id)
        return os.getpid()

    with kb.connect() as conn:
        first = kb.create_task(conn, title="first owner envelope", assignee="alice")
        second = kb.create_task(conn, title="second owner envelope", assignee="alice")
        first_result = kb.dispatch_once(
            conn,
            spawn_fn=spawn,
            target_task_id=first,
            max_in_progress=1,
            reconcile_orphans=False,
        )
        second_result = kb.dispatch_once(
            conn,
            spawn_fn=spawn,
            target_task_id=second,
            max_in_progress=1,
            reconcile_orphans=False,
        )

        assert [row[0] for row in first_result.spawned] == [first]
        assert second_result.spawned == []
        assert spawned == [first]
        assert kb.get_task(conn, second).status == "ready"


class _closed_context:
    def __enter__(self):
        return object()

    def __exit__(self, *_args):
        return False
