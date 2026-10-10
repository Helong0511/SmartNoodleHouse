"""订单详情：品项规格一次查出，避免 N+1 拖慢支付后结果页。"""
from __future__ import annotations

import ast
from pathlib import Path


def test_get_order_batches_item_options():
    src = Path(__file__).resolve().parents[1] / "backend" / "app" / "api" / "orders.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    fn = None
    for node in tree.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "get_order":
            fn = node
            break
    assert fn is not None, "未找到 get_order"
    text = ast.get_source_segment(src.read_text(encoding="utf-8"), fn) or ""
    # 禁止在循环里按 order_item_id 单条查规格
    assert "OrderItemOption.order_item_id == item.id" not in text
    assert "order_item_id.in_" in text or "OrderItemOption.order_item_id.in_" in text
