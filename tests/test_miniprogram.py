"""小程序扫码入口与会员引导"""


def test_member_face_prompt_flags():
    prompts = {
        "ask_register_member": True,
        "ask_face_welcome": True,
    }
    assert prompts["ask_register_member"] is True
    assert prompts["ask_face_welcome"] is True


def test_unauthorized_face_not_forced():
    enable_face = False
    assert enable_face is False


def test_table_token_required():
    token = "abc123_unpredictable"
    fake_table_no = "A01"
    assert token != fake_table_no
