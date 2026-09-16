# -*- coding: utf-8 -*-
"""广告追踪引擎中文冒烟测试。"""
from tracking import TrackingEngine, TrackingError

KEY1 = "11" * 32
KEY2 = "22" * 32

print("=" * 72)
print("广告追踪 ID Engine —— 中文冒烟测试")
print("=" * 72)

engine = TrackingEngine()
engine.set_key(1, KEY1)
engine.set_current_kid(1)
print("[通过] KID=1 密钥加载成功")

token = engine.generate({
    "offer_id": 10001,
    "campaign_id": 20001,
    "publisher_id": 30001,
    "advertiser_id": 40001,
    "source": "facebook",
    "fbclid": "example",
    "gclid": "example",
}, 3600)
print("[通过] Token 生成成功")
print("Token：", token)

verified = engine.verify(token)
print("[通过] 合法 Token 验证成功")
print("验证结果：", verified)

parsed = engine.parse(token)
assert parsed["click_id"] == verified["click_id"]
print("[通过] Token 解析成功")

# 篡改一个中间字符，必须被 AES-GCM 完整性校验拒绝。
tampered = token[:len(token)//2] + ("A" if token[len(token)//2] != "A" else "B") + token[len(token)//2+1:]
try:
    engine.verify(tampered)
except TrackingError as exc:
    print("[通过] 篡改 Token 已拒绝：", exc)
else:
    raise AssertionError("严重错误：篡改 Token 被接受")

for bad in ["", "abc", "!!!!", "A" * 9000]:
    try:
        engine.verify(bad)
    except (TrackingError, UnicodeEncodeError):
        continue
    else:
        raise AssertionError(f"严重错误：非法 Token 被接受：{bad[:20]!r}")
print("[通过] 非法 Token 全部拒绝")

engine.set_key(2, KEY2)
old = token
engine.set_current_kid(2)
new = engine.generate({
    "offer_id": 10001,
    "campaign_id": 20001,
    "publisher_id": 30001,
    "advertiser_id": 40001,
}, 3600)
assert engine.verify(old)["kid"] == 1
assert engine.verify(new)["kid"] == 2
print("[通过] KID 密钥轮换成功：旧 Token 可验证，新 Token 使用 KID=2")

print("=" * 72)
print("冒烟测试结论：PASS")
print("所有核心安全检查均通过。")
print("=" * 72)
