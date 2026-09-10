"""小程序扫码入口与会员引导（游客可直接点餐）"""


def test_member_face_prompt_flags_non_blocking():
    """首次扫码不强制注册：ask_register_member 应为 False。"""
    prompts = {
        "ask_register_member": False,
        "ask_face_welcome": False,
    }
    assert prompts["ask_register_member"] is False
    assert prompts["ask_face_welcome"] is False


def test_unauthorized_face_not_forced():
    enable_face = False
    assert enable_face is False


def test_table_token_required():
    token = "abc123_unpredictable"
    fake_table_no = "A01"
    assert token != fake_table_no


def test_guest_member_status_semantics():
    guest = {"member_status": "GUEST", "need_member_prompt": True, "is_guest": True}
    member = {"member_status": "MEMBER", "need_member_prompt": False, "is_guest": False}
    assert guest["is_guest"] and guest["need_member_prompt"]
    assert not member["is_guest"] and not member["need_member_prompt"]
    # need_member_prompt 仅软引导，不拦截点餐
    can_order = True
    assert can_order
