"""Payment sync endpoints must commit durable state before realtime notification."""
import ast
import inspect

from app.api import payments as payment_api


def _top_level_statement_index(function, awaited_call_name: str) -> int:
    tree = ast.parse(inspect.getsource(function))
    function_node = next(
        node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    )
    for index, statement in enumerate(function_node.body):
        if any(
            isinstance(node, ast.Await)
            and isinstance(node.value, ast.Call)
            and (
                isinstance(node.value.func, ast.Attribute)
                and node.value.func.attr == awaited_call_name
            )
            for node in ast.walk(statement)
        ):
            return index
    raise AssertionError(f"awaited call {awaited_call_name!r} not found")


def _assert_commit_precedes_notification(function):
    commit_index = _top_level_statement_index(function, "commit")
    notification_index = _top_level_statement_index(function, "_notify_paid_or_refunded")
    assert commit_index < notification_index, (
        f"{function.__name__} must commit the payment state before notifying WebSocket clients"
    )


def test_order_payment_sync_commits_before_notification():
    _assert_commit_precedes_notification(payment_api.sync_order_payment)


def test_single_payment_sync_commits_before_notification():
    _assert_commit_precedes_notification(payment_api.sync_payment)
