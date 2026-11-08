from __future__ import annotations

import bcrypt

from app.core.security import (
    hash_password,
    verify_password,
    verify_password_and_update,
)


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


def test_malformed_or_unknown_hash_fails_closed():
    valid, upgraded = verify_password_and_update("any-password", "not-a-valid-password-hash")

    assert valid is False
    assert upgraded is None
