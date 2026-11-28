"""Merged point-ledger API must identify the historical source account."""
import inspect

from app.api.miniprogram_customer import get_my_points_ledger


def test_points_ledger_response_exposes_origin_and_snapshot_semantics():
    source = inspect.getsource(get_my_points_ledger)
    assert '"origin_customer_id": origin_id' in source
    assert 'if r.origin_customer_id is not None else None' in source
    assert 'if origin_id is not None else None' in source
    assert 'origin_id != int(customer.id) if origin_id is not None else None' in source
    assert 'else int(r.customer_id)' not in source
    assert '"origin_customer_no": origin_customer.customer_no if origin_customer else None' in source
    assert '"origin_customer_name": origin_customer.display_name if origin_customer else None' in source
    assert '"is_merged_history": is_merged_history' in source
    assert '"原账户历史积分余额快照"' in source
    assert 'if is_merged_history is True' in source
    assert "PointLedger.customer_id == int(customer.id)" in source
