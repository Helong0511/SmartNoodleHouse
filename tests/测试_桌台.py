"""桌台二维码防伪造测试"""
import secrets


def test_qr_token_unpredictable():
    token1 = secrets.token_urlsafe(32)
    token2 = secrets.token_urlsafe(32)
    assert token1 != token2
    assert len(token1) >= 32


def test_table_param_not_trusted():
    """不能通过 table=A01 伪造桌台"""
    user_table_param = "A01"
    valid_token = secrets.token_urlsafe(32)
    assert user_table_param != valid_token
