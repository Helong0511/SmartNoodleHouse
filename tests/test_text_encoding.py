from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "check_text_encoding", ROOT / "scripts" / "check_text_encoding.py"
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_utf8_chinese_and_supplementary_unicode_are_valid(monkeypatch, tmp_path):
    monkeypatch.setattr(MODULE, "ROOT", tmp_path)
    path = tmp_path / "example.py"

    assert MODULE.validate_bytes(path, "面馆订单 🍜\n".encode("utf-8")) == []


def test_utf8_bom_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(MODULE, "ROOT", tmp_path)
    path = tmp_path / "example.py"

    assert "UTF-8 BOM" in MODULE.validate_bytes(path, b"\xef\xbb\xbfprint(1)\n")[0]


def test_invalid_utf8_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(MODULE, "ROOT", tmp_path)
    path = tmp_path / "example.py"

    assert "invalid UTF-8" in MODULE.validate_bytes(path, b"\xff\xfe")[0]


def test_replacement_character_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(MODULE, "ROOT", tmp_path)
    path = tmp_path / "example.py"

    assert "U+FFFD" in MODULE.validate_bytes(path, ("乱码" + chr(0xFFFD)).encode("utf-8"))[0]


def test_crlf_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(MODULE, "ROOT", tmp_path)
    path = tmp_path / "example.py"

    assert "CR/CRLF" in MODULE.validate_bytes(path, b"line1\r\nline2\r\n")[0]


def test_json_serialization_preserves_chinese_and_four_byte_unicode():
    from app.core.response import dumps_json

    payload = {"dish": "牛肉面", "emoji": "🍜"}
    encoded = dumps_json(payload)

    assert encoded == '{"dish": "牛肉面", "emoji": "🍜"}'
    assert json.loads(encoded) == payload
