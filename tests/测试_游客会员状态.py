"""游客/会员状态单元测试"""


def test_member_payload_uses_member_status():
    from app.services.customer_service import CustomerService

    class C:
        member_status = "GUEST"
        account_status = "ACTIVE"
        profile_status = "EMPTY"
        phone_verified = 0
        member_activated_at = None
        member_level_id = None
        points = 0

    payload = CustomerService.member_payload(C())
    assert payload["is_member"] is False
    assert payload["member_status"] == "GUEST"

    class M:
        member_status = "MEMBER"
        account_status = "ACTIVE"
        profile_status = "PARTIAL"
        phone_verified = 1
        member_activated_at = None
        member_level_id = 1
        points = 10

    payload2 = CustomerService.member_payload(M())
    assert payload2["is_member"] is True
    assert payload2["member_status"] == "MEMBER"


def test_profile_status_compute():
    from app.services.customer_service import CustomerService

    class C:
        nickname = None
        display_name = None
        phone = None
        avatar_url = None

    assert CustomerService.compute_profile_status(C()) == "EMPTY"

    class P:
        nickname = "张三"
        display_name = None
        phone = None
        avatar_url = None

    assert CustomerService.compute_profile_status(P()) == "PARTIAL"

    class Done:
        nickname = "张三"
        display_name = None
        phone = "13800138000"
        avatar_url = "/uploads/a.jpg"

    assert CustomerService.compute_profile_status(Done()) == "COMPLETED"


def test_is_member_helper():
    from app.services.customer_service import CustomerService

    class G:
        member_status = "GUEST"

    class M:
        member_status = "MEMBER"

    assert CustomerService.is_member(G()) is False
    assert CustomerService.is_member(M()) is True
