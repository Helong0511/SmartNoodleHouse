"""顾客详情字段级权限回归测试。"""
from app.services.field_policy import apply_customer_detail_policy


def test_customer_detail_hides_nested_provider_ids_and_birthday_without_pii_permission():
    payload = {
        "phone": "13800138000",
        "birthday": "1990-01-02",
        "birthday_display": "公历 1 月 2 日",
        "wechat_accounts": [{"openid": "wx-private", "unionid": "union-private", "nickname": "顾客"}],
        "alipay_accounts": [{"alipay_user_id": "ali-private", "nickname": "顾客"}],
    }

    result = apply_customer_detail_policy(payload, [])

    assert result["phone"] == "138****8000"
    assert "birthday" not in result
    assert "birthday_display" not in result
    assert "openid" not in result["wechat_accounts"][0]
    assert "unionid" not in result["wechat_accounts"][0]
    assert "alipay_user_id" not in result["alipay_accounts"][0]
    assert result["wechat_accounts"][0]["nickname"] == "顾客"


def test_customer_detail_keeps_pii_for_explicit_pii_permission():
    payload = {
        "phone": "13800138000",
        "birthday": "1990-01-02",
        "wechat_accounts": [{"openid": "wx-private", "unionid": "union-private"}],
        "alipay_accounts": [{"alipay_user_id": "ali-private"}],
    }

    result = apply_customer_detail_policy(payload, ["customer:pii"])

    assert result["phone"] == "13800138000"
    assert result["birthday"] == "1990-01-02"
    assert result["wechat_accounts"][0]["openid"] == "wx-private"
    assert result["wechat_accounts"][0]["unionid"] == "union-private"
    assert result["alipay_accounts"][0]["alipay_user_id"] == "ali-private"


def test_customer_detail_super_admin_bypasses_field_policy():
    payload = {
        "phone": "13800138000",
        "birthday": "1990-01-02",
        "wechat_accounts": [{"openid": "wx-private"}],
    }

    result = apply_customer_detail_policy(payload, [], is_super_admin=True)

    assert result["phone"] == "13800138000"
    assert result["birthday"] == "1990-01-02"
    assert result["wechat_accounts"][0]["openid"] == "wx-private"
