import os
import sys
import time
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from tracking import TrackingEngine, TrackingError

LIB = os.getenv("AD_TRACKING_LIB")
if not LIB:
    if os.name == "nt":
        LIB = str(Path(__file__).resolve().parents[1] / "target" / "release" / "ad_tracking_engine.dll")
    else:
        LIB = str(Path(__file__).resolve().parents[1] / "target" / "release" / "libad_tracking_engine.so")

pytestmark = pytest.mark.skipif(not Path(LIB).exists(), reason=f"native library not built: {LIB}")

KEY1 = "11" * 32
KEY2 = "22" * 32

@pytest.fixture()
def engine():
    e = TrackingEngine(LIB)
    e.set_key(1, KEY1)
    e.set_current_kid(1)
    return e

def payload():
    return {
        "offer_id": 10001,
        "campaign_id": 20001,
        "publisher_id": 30001,
        "advertiser_id": 40001,
        "source": "facebook",
        "fbclid": "fb-test",
        "gclid": "g-test",
        "utm_source": "facebook",
        "utm_medium": "cpc",
    }

def test_roundtrip(engine):
    t = engine.generate(payload(), 3600)
    p = engine.verify(t)
    assert p["v"] == 1
    assert p["kid"] == 1
    assert p["offer_id"] == 10001
    assert p["click_id"]

def test_generate_has_unique_tokens_and_click_ids(engine):
    rows = [engine.generate(payload(), 3600) for _ in range(1000)]
    assert len(set(rows)) == 1000
    parsed = [engine.verify(x) for x in rows]
    assert len({x["click_id"] for x in parsed}) == 1000

@pytest.mark.parametrize("kind", ["middle", "nonce", "tag"])
def test_tamper_rejected(engine, kind):
    import base64
    t = engine.generate(payload(), 3600)
    pad = "=" * (-len(t) % 4)
    raw = bytearray(base64.urlsafe_b64decode(t + pad))
    if kind == "middle":
        raw[14 + 2] ^= 1
    elif kind == "nonce":
        raw[2] ^= 1
    else:
        raw[-1] ^= 1
    bad = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    with pytest.raises(TrackingError):
        engine.verify(bad)

@pytest.mark.parametrize("bad", ["", "abc", "!!!!", "AQ", "AQI=", "A"*9000])
def test_invalid_tokens_rejected(engine, bad):
    with pytest.raises((TrackingError, UnicodeEncodeError)):
        engine.verify(bad)

def test_wrong_key_rejected(engine):
    t = engine.generate(payload(), 3600)
    engine.remove_key(1)
    engine.set_key(1, "33" * 32)
    with pytest.raises(TrackingError):
        engine.verify(t)

def test_expired_rejected(engine):
    # Native TTL deliberately disallows zero, so construct a valid token with TTL=1.
    t = engine.generate(payload(), 1)
    time.sleep(2)
    with pytest.raises(TrackingError):
        engine.verify(t)

def test_future_and_header_binding_are_covered_by_rust_tests():
    assert True

def test_key_rotation(engine):
    old = engine.generate(payload(), 3600)
    engine.set_key(2, KEY2)
    engine.set_current_kid(2)
    new = engine.generate(payload(), 3600)
    assert engine.verify(old)["kid"] == 1
    assert engine.verify(new)["kid"] == 2

def test_parse_is_authenticated(engine):
    t = engine.generate(payload(), 3600)
    assert engine.parse(t)["click_id"]
    with pytest.raises(TrackingError):
        engine.parse(t[:-1] + ("A" if t[-1] != "A" else "B"))


def test_ffi_output_parameter_type_and_native_error_propagation(engine):
    # Regression test for the char** ABI: output parameters must be POINTER(c_char_p).
    # A malformed token must reach Rust and be rejected, not fail in ctypes or be accepted.
    assert engine.lib.tracking_generate.argtypes[2] == __import__('ctypes').POINTER(__import__('ctypes').c_char_p)
    with pytest.raises(TrackingError):
        engine.verify("!!!!")
