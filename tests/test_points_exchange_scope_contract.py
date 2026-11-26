from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_points_exchange_mutation_enforces_feature_gate_and_store_scope() -> None:
    source = (ROOT / "backend/app/services/customer_service.py").read_text(encoding="utf-8")
    start = source.index("async def exchange_points_for_coupon(")
    end = source.index("    @staticmethod", start + 20)
    method = source[start:end]
    assert '"points_exchange_enabled"' in method
    assert "option.store_id" in method
    assert "template.store_id" in method
    assert "store_whitelist" in method
    assert "cost <= 0" in method
