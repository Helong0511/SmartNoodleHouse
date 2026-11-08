from __future__ import annotations

import asyncio

import bcrypt
import pytest

from app.core.security import (
    hash_password,
    hash_password_async,
    verify_password,
    verify_password_and_update,
    verify_password_and_update_async,
)


@pytest.mark.asyncio
async def test_async_password_hashing_wrappers_roundtrip_without_blocking_api_loop():
    encoded = await hash_password_async("Async-Staff-Password!")
    valid, upgraded = await verify_password_and_update_async(
        "Async-Staff-Password!", encoded
    )

    assert encoded.startswith("$argon2id$")
    assert valid is True
    assert upgraded is None


@pytest.mark.asyncio
async def test_argon2_async_wrappers_limit_concurrency_per_event_loop(monkeypatch):
    from app.core import security

    active = 0
    max_active = 0

    def fake_hash(password: str) -> str:
        return password

    async def fake_run_in_threadpool(func, *args):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        try:
            await asyncio.sleep(0.01)
            return func(*args)
        finally:
            active -= 1

    monkeypatch.setattr(security, "hash_password", fake_hash)
    monkeypatch.setattr(security, "run_in_threadpool", fake_run_in_threadpool)

    results = await asyncio.gather(
        *(security.hash_password_async(f"password-{index}") for index in range(8))
    )

    assert len(results) == 8
    assert max_active <= 2


def test_new_password_hashes_use_argon2id():
    encoded = hash_password("SmartNoodle-Password-2026!")

    assert encoded.startswith("$argon2id$")
    assert verify_password("SmartNoodle-Password-2026!", encoded)
    assert not verify_password("incorrect-password", encoded)


def test_legacy_passlib_bcrypt_hash_is_verified_and_upgraded_to_argon2id():
    # Passlib bcrypt 哈希与标准 bcrypt $2b$ 编码兼容；模拟已有数据库用户。
    plain = "Existing-Staff-Password!"
    legacy_hash = bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("ascii")

    valid, upgraded = verify_password_and_update(plain, legacy_hash)

    assert valid is True
    assert upgraded is not None
    assert upgraded.startswith("$argon2id$")
    assert verify_password(plain, upgraded)


def test_wrong_password_does_not_produce_an_upgrade_hash():
    plain = "Existing-Staff-Password!"
    legacy_hash = bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("ascii")

    valid, upgraded = verify_password_and_update("wrong-password", legacy_hash)

    assert valid is False
    assert upgraded is None


def test_malformed_argon2_hash_fails_closed():
    # 前缀可识别但编码体损坏时，底层 argon2-cffi 会抛 InvalidHashError。
    valid, upgraded = verify_password_and_update(
        "any-password",
        "$argon2id$v=19$m=65536,t=3,p=4$invalid$invalid",
    )

    assert valid is False
    assert upgraded is None


def test_malformed_or_unknown_hash_fails_closed():
    valid, upgraded = verify_password_and_update("any-password", "not-a-valid-password-hash")

    assert valid is False
    assert upgraded is None
