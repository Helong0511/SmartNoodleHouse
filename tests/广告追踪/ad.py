#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
AES-256-GCM 广告追踪 ID / Tracking Token 测试程序

安装：
    pip install cryptography

运行：
    python -X utf8 "E:\SmartNoodleHouse\tests\广告追踪\ad.py"
    python -X utf8 "E:\SmartNoodleHouse\tests\广告追踪\ad_tracking_dll\tests\stress_test.py"
生成一个 32 字节 AES-256 Key
    python -c "import secrets; print(secrets.token_hex(32))"
功能：
1. 生成 click_id
2. 构造广告溯源 Payload
3. AES-256-GCM 加密
4. Base64URL 编码成 tracking_token
5. 解析 / 解密 tracking_token
6. 验证 token 是否被篡改
7. 验证过期时间
8. 演示修改 token 后验证失败
9. 演示错误 Key 无法解密
10. 支持 KID（Key ID）密钥轮换的基础结构

注意：
- 这里是测试程序。
- 生产环境不要把密钥写死在代码中。
- nonce 必须使用 secrets.token_bytes(12) 每次随机生成，不能重复。
"""

import base64
import json
import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


# ============================================================
# 配置
# ============================================================

VERSION = 1
CURRENT_KID = 1

# 测试环境密钥。
# AES-256 = 32 bytes = 256 bits
TEST_KEY = secrets.token_bytes(32)

# AAD：参与认证，但不会被加密。
AAD = b"ad-tracking:v1"


# ============================================================
# 工具函数
# ============================================================

def b64url_encode(data: bytes) -> str:
    """Base64URL，不带 '=' padding。"""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(value: str) -> bytes:
    """Base64URL 解码。"""
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def generate_click_id() -> str:
    """
    这里使用 UUID4 做演示。
    生产环境广告系统建议考虑 UUIDv7 / ULID，
    方便按时间排序和数据库索引。
    """
    return str(uuid.uuid4())


# ============================================================
# Tracking Token Service
# ============================================================

@dataclass
class TrackingTokenService:
    keys: Dict[int, bytes]
    current_kid: int = CURRENT_KID
    aad: bytes = AAD

    def __post_init__(self):
        for kid, key in self.keys.items():
            if len(key) != 32:
                raise ValueError(
                    f"KID={kid} 的 AES-256 Key 必须是 32 bytes，"
                    f"当前是 {len(key)} bytes"
                )

    def generate_payload(
        self,
        click_id: str,
        offer_id: int,
        campaign_id: int,
        publisher_id: int,
        advertiser_id: int,
        sub_id: str = "",
        source: str = "",
        fbclid: str = "",
        gclid: str = "",
        utm_source: str = "",
        utm_medium: str = "",
        utm_campaign: str = "",
        ttl_seconds: int = 30 * 24 * 3600,
    ) -> Dict[str, Any]:
        """
        构造广告溯源信息。

        注意：
        click_id 是真正的业务唯一 ID；
        tracking_token 是它的安全传输载体。
        """
        now = int(time.time())

        return {
            "v": VERSION,
            "kid": self.current_kid,
            "iat": now,
            "exp": now + ttl_seconds,

            "click_id": click_id,

            "offer_id": offer_id,
            "campaign_id": campaign_id,
            "publisher_id": publisher_id,
            "advertiser_id": advertiser_id,

            "sub_id": sub_id,
            "source": source,

            "fbclid": fbclid,
            "gclid": gclid,

            "utm_source": utm_source,
            "utm_medium": utm_medium,
            "utm_campaign": utm_campaign,
        }

    def encrypt(self, payload: Dict[str, Any]) -> str:
        """
        Payload -> AES-256-GCM -> Base64URL Tracking Token

        Token 二进制结构：

            version  : 1 byte
            kid      : 1 byte
            nonce    : 12 bytes
            ciphertext + tag : N bytes

        AESGCM.encrypt() 返回：
            ciphertext + 16-byte authentication tag
        """
        kid = int(payload["kid"])

        if kid not in self.keys:
            raise ValueError(f"未知 KID: {kid}")

        key = self.keys[kid]

        # GCM 推荐 12 bytes nonce。
        # 每次加密必须生成新的 nonce。
        nonce = secrets.token_bytes(12)

        plaintext = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

        aesgcm = AESGCM(key)

        # AAD 不加密，但会参与完整性认证。
        ciphertext_and_tag = aesgcm.encrypt(
            nonce,
            plaintext,
            self.aad,
        )

        # 外层结构：
        # Version + KID + Nonce + Ciphertext+Tag
        token_bytes = (
            bytes([VERSION])
            + bytes([kid])
            + nonce
            + ciphertext_and_tag
        )

        return b64url_encode(token_bytes)

    def decrypt(self, token: str) -> Dict[str, Any]:
        """
        Tracking Token -> Base64URL -> AES-256-GCM 解密

        如果 Token 被修改：
            InvalidTag
            -> 验证失败
        """
        try:
            raw = b64url_decode(token)
        except Exception as e:
            raise ValueError("Token 不是合法的 Base64URL") from e

        # 最小：
        # version 1
        # kid 1
        # nonce 12
        # tag 16
        minimum_length = 1 + 1 + 12 + 16

        if len(raw) < minimum_length:
            raise ValueError("Token 长度非法")

        version = raw[0]
        kid = raw[1]

        if version != VERSION:
            raise ValueError(f"不支持的 Token Version: {version}")

        if kid not in self.keys:
            raise ValueError(f"未知 KID: {kid}")

        nonce = raw[2:14]
        ciphertext_and_tag = raw[14:]

        aesgcm = AESGCM(self.keys[kid])

        try:
            plaintext = aesgcm.decrypt(
                nonce,
                ciphertext_and_tag,
                self.aad,
            )
        except InvalidTag as e:
            raise ValueError(
                "Token 密码学验证失败：Token 可能被篡改、"
                "Key 错误或 AAD 不一致"
            ) from e

        try:
            payload = json.loads(plaintext.decode("utf-8"))
        except Exception as e:
            raise ValueError("Token 解密成功，但 Payload 格式非法") from e

        return payload

    def verify(
        self,
        token: str,
        check_expiration: bool = True,
    ) -> Dict[str, Any]:
        """
        完整验证：

        1. Token 格式
        2. Version
        3. KID
        4. AES-GCM Authentication Tag
        5. Payload
        6. click_id
        7. 时间
        """
        payload = self.decrypt(token)

        required_fields = [
            "v",
            "kid",
            "iat",
            "exp",
            "click_id",
            "offer_id",
            "campaign_id",
            "publisher_id",
            "advertiser_id",
        ]

        for field in required_fields:
            if field not in payload:
                raise ValueError(f"缺少必要字段: {field}")

        if payload["v"] != VERSION:
            raise ValueError("Payload Version 不正确")

        if not payload["click_id"]:
            raise ValueError("click_id 不能为空")

        now = int(time.time())

        if check_expiration:
            if now > int(payload["exp"]):
                raise ValueError("Tracking Token 已过期")

        if int(payload["iat"]) > now + 300:
            raise ValueError("Token 的 iat 超出允许的未来时间范围")

        return payload


# ============================================================
# 测试
# ============================================================

def print_payload(payload: Dict[str, Any]):
    print(json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
    ))


def test_normal_flow(service: TrackingTokenService):
    print("\n" + "=" * 70)
    print("TEST 1：正常生成 / 加密 / 解析 / 解密 / 验证")
    print("=" * 70)

    click_id = generate_click_id()

    print(f"\n[1] click_id:")
    print(click_id)

    payload = service.generate_payload(
        click_id=click_id,
        offer_id=10001,
        campaign_id=20001,
        publisher_id=30001,
        advertiser_id=40001,
        sub_id="publisher_sub_001",
        source="facebook",
        fbclid="IwZXh0bgNhZW0CMTEA...",
        gclid="CjwKCA...",
        utm_source="facebook",
        utm_medium="cpc",
        utm_campaign="summer_2026",
    )

    print("\n[2] 原始 Payload:")
    print_payload(payload)

    token = service.encrypt(payload)

    print("\n[3] tracking_token:")
    print(token)

    print(f"\n[4] Token 长度: {len(token)} 字符")

    decoded = service.verify(token)

    print("\n[5] 解密 + 验证结果:")
    print_payload(decoded)

    print("\n[6] click_id 是否一致:")
    print(decoded["click_id"] == click_id)


def test_tamper(service: TrackingTokenService):
    print("\n" + "=" * 70)
    print("TEST 2：篡改 Tracking Token")
    print("=" * 70)

    payload = service.generate_payload(
        click_id=generate_click_id(),
        offer_id=10001,
        campaign_id=20001,
        publisher_id=30001,
        advertiser_id=40001,
    )

    token = service.encrypt(payload)

    print("\n正常 Token:")
    print(token)

    # 修改最后一个字符
    last = token[-1]
    replacement = "A" if last != "A" else "B"
    tampered = token[:-1] + replacement

    print("\n篡改后的 Token:")
    print(tampered)

    try:
        service.verify(tampered)
        print("\n❌ 安全测试失败：篡改后的 Token 居然验证成功")
    except ValueError as e:
        print("\n✅ 安全测试通过：篡改 Token 被拒绝")
        print("原因:", e)


def test_wrong_key(service: TrackingTokenService):
    print("\n" + "=" * 70)
    print("TEST 3：错误 Key")
    print("=" * 70)

    payload = service.generate_payload(
        click_id=generate_click_id(),
        offer_id=10001,
        campaign_id=20001,
        publisher_id=30001,
        advertiser_id=40001,
    )

    token = service.encrypt(payload)

    attacker_service = TrackingTokenService(
        keys={
            1: secrets.token_bytes(32)
        }
    )

    try:
        attacker_service.verify(token)
        print("\n❌ 安全测试失败：错误 Key 居然可以解密")
    except ValueError as e:
        print("\n✅ 安全测试通过：错误 Key 无法解密")
        print("原因:", e)


def test_expiration():
    print("\n" + "=" * 70)
    print("TEST 4：Token 过期")
    print("=" * 70)

    service = TrackingTokenService(
        keys={1: secrets.token_bytes(32)}
    )

    payload = service.generate_payload(
        click_id=generate_click_id(),
        offer_id=10001,
        campaign_id=20001,
        publisher_id=30001,
        advertiser_id=40001,
        ttl_seconds=-1,  # 立即过期
    )

    token = service.encrypt(payload)

    try:
        service.verify(token)
        print("\n❌ 过期测试失败：过期 Token 居然通过")
    except ValueError as e:
        print("\n✅ 过期测试通过")
        print("原因:", e)


def test_key_rotation():
    print("\n" + "=" * 70)
    print("TEST 5：KID 密钥轮换")
    print("=" * 70)

    old_key = secrets.token_bytes(32)
    new_key = secrets.token_bytes(32)

    # 老系统
    old_service = TrackingTokenService(
        keys={1: old_key},
        current_kid=1,
    )

    old_payload = old_service.generate_payload(
        click_id=generate_click_id(),
        offer_id=10001,
        campaign_id=20001,
        publisher_id=30001,
        advertiser_id=40001,
    )

    old_token = old_service.encrypt(old_payload)

    print("\n老 Token 已生成，KID=1")
    print(old_token)

    # 新系统同时保留旧 Key
    new_service = TrackingTokenService(
        keys={
            1: old_key,
            2: new_key,
        },
        current_kid=2,
    )

    new_payload = new_service.generate_payload(
        click_id=generate_click_id(),
        offer_id=10001,
        campaign_id=20001,
        publisher_id=30001,
        advertiser_id=40001,
    )

    new_token = new_service.encrypt(new_payload)

    print("\n新 Token 已生成，KID=2")
    print(new_token)

    # 新系统可以解密旧 Token
    old_decoded = new_service.verify(old_token)

    # 新 Token 使用新 Key
    new_decoded = new_service.verify(new_token)

    print("\n旧 Token 解密成功:")
    print("KID =", old_decoded["kid"])

    print("\n新 Token 解密成功:")
    print("KID =", new_decoded["kid"])

    print("\n✅ KID 密钥轮换测试通过")


def test_many_tokens(service: TrackingTokenService, count: int = 10000):
    print("\n" + "=" * 70)
    print(f"TEST 6：批量生成 / 解密 / 验证 {count:,} 个 Token")
    print("=" * 70)

    start = time.perf_counter()

    tokens = []

    for _ in range(count):
        payload = service.generate_payload(
            click_id=generate_click_id(),
            offer_id=10001,
            campaign_id=20001,
            publisher_id=30001,
            advertiser_id=40001,
        )

        tokens.append(service.encrypt(payload))

    encrypt_time = time.perf_counter() - start

    start = time.perf_counter()

    for token in tokens:
        service.verify(token)

    decrypt_time = time.perf_counter() - start

    print(f"\n生成 + 加密：{encrypt_time:.4f} 秒")
    print(f"平均：{encrypt_time / count * 1000:.4f} ms/token")

    print(f"\n解密 + 验证：{decrypt_time:.4f} 秒")
    print(f"平均：{decrypt_time / count * 1000:.4f} ms/token")

    print("\n✅ 批量测试全部通过")


# ============================================================
# Main
# ============================================================

def main():
    print("=" * 70)
    print("AES-256-GCM 广告追踪 ID / Tracking Token 测试程序")
    print("=" * 70)

    print("\n算法:")
    print("  AES-256-GCM")

    print("\nKey:")
    print(f"  {len(TEST_KEY)} bytes / 256 bits")

    print("\nNonce:")
    print("  12 bytes，每次随机生成")

    print("\nAuthentication Tag:")
    print("  16 bytes")

    print("\nAAD:")
    print(f"  {AAD!r}")

    service = TrackingTokenService(
        keys={
            CURRENT_KID: TEST_KEY,
        },
        current_kid=CURRENT_KID,
    )

    test_normal_flow(service)
    test_tamper(service)
    test_wrong_key(service)
    test_expiration()
    test_key_rotation()
    test_many_tokens(service, count=10000)

    print("\n" + "=" * 70)
    print("全部测试完成")
    print("=" * 70)


if __name__ == "__main__":
    main()
