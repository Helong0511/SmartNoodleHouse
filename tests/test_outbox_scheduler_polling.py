"""Regression guard for the Outbox polling cadence exposed in scheduler metadata."""
from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCHEDULER_PATH = ROOT / "backend" / "app" / "tasks" / "scheduler.py"


def _literal_dict(node: ast.AST) -> dict[str, ast.AST]:
    if not isinstance(node, ast.Dict):
        raise AssertionError("expected a dictionary literal")
    result: dict[str, ast.AST] = {}
    for key, value in zip(node.keys, node.values):
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            result[key.value] = value
    return result


def test_outbox_polling_interval_matches_scheduler_catalog() -> None:
    tree = ast.parse(SCHEDULER_PATH.read_text(encoding="utf-8"))
    catalog = next(
        node.value
        for node in tree.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "TASK_CATALOG"
    )
    assert isinstance(catalog, ast.List)
    metadata = next(
        values
        for item in catalog.elts
        if isinstance(item, ast.Dict)
        for values in [_literal_dict(item)]
        if isinstance(values.get("id"), ast.Constant)
        and values["id"].value == "drain_outbox_events"
    )
    schedule = _literal_dict(metadata["default_schedule"])
    assert isinstance(schedule["every"], ast.Constant)
    assert schedule["every"].value == 5

    register_tasks = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "register_tasks"
    )
    drain_job = next(
        node for node in ast.walk(register_tasks)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "drain_outbox_events"
    )
    scheduled_job = next(
        decorator for decorator in drain_job.decorator_list
        if isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Attribute)
        and decorator.func.attr == "scheduled_job"
    )
    assert scheduled_job.args[0] == ast.Constant("interval")
    seconds = next(keyword.value for keyword in scheduled_job.keywords if keyword.arg == "seconds")
    assert isinstance(seconds, ast.Constant)
    assert seconds.value == schedule["every"].value


def test_outbox_polling_keeps_bounded_batch_size() -> None:
    tree = ast.parse(SCHEDULER_PATH.read_text(encoding="utf-8"))
    register_tasks = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "register_tasks"
    )
    drain_job = next(
        node for node in ast.walk(register_tasks)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "drain_outbox_events"
    )
    call = next(
        node for node in ast.walk(drain_job)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "process_pending_outbox"
    )
    limit = next(keyword.value for keyword in call.keywords if keyword.arg == "limit")
    assert isinstance(limit, ast.Constant)
    assert limit.value == 30
