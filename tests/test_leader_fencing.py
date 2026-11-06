from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core.leader_fencing import (
    LeaderFenceLostError,
    _before_commit_fence,
    activate_leader_epoch,
    deactivate_leader_epoch,
    leader_fence_scope,
)


def _session(dialect, row):
    session = MagicMock()
    session.get_bind.return_value.dialect.name = dialect
    session.execute.return_value.first.return_value = row
    return session


def test_mysql_commit_fence_locks_singleton_row_and_accepts_current_epoch():
    activate_leader_epoch(17, "worker-a")
    try:
        session = _session("mysql", (17, "worker-a"))
        with leader_fence_scope():
            _before_commit_fence(session)
        sql = str(session.execute.call_args.args[0])
        assert "FOR UPDATE" in sql
        assert "leader_fencing_state" in sql
    finally:
        deactivate_leader_epoch("worker-a")


def test_commit_fence_rejects_stale_leader_epoch():
    activate_leader_epoch(17, "worker-a")
    try:
        session = _session("mysql", (18, "worker-b"))
        with leader_fence_scope():
            with pytest.raises(LeaderFenceLostError):
                _before_commit_fence(session)
    finally:
        deactivate_leader_epoch("worker-a")


def test_commit_fence_rejects_missing_fencing_row():
    activate_leader_epoch(17, "worker-a")
    try:
        session = _session("mysql", None)
        with leader_fence_scope():
            with pytest.raises(LeaderFenceLostError):
                _before_commit_fence(session)
    finally:
        deactivate_leader_epoch("worker-a")


def test_sqlite_fence_still_checks_epoch_without_for_update():
    activate_leader_epoch(17, "worker-a")
    try:
        session = _session("sqlite", (17, "worker-a"))
        with leader_fence_scope():
            _before_commit_fence(session)
        sql = str(session.execute.call_args.args[0])
        assert "FOR UPDATE" not in sql
    finally:
        deactivate_leader_epoch("worker-a")
