"""logo_overlay：小图也不应出现负偏移；不得低于 min_diameter。"""
from io import BytesIO

from PIL import Image, ImageDraw

from app.utils.logo_overlay import _fit_logo_geometry, overlay_center_logo


def _solid_png(size: int, color=(240, 240, 240, 255)) -> bytes:
    im = Image.new("RGBA", (size, size), color)
    buf = BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def _logo_png(size: int = 64) -> bytes:
    im = Image.new("RGBA", (size, size), (255, 0, 0, 255))
    ImageDraw.Draw(im).rectangle((8, 8, size - 9, size - 9), fill=(0, 128, 255, 255))
    buf = BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def test_fit_geometry_never_exceeds_side_or_min_diameter():
    for side in (32, 48, 64, 120, 280):
        geom = _fit_logo_geometry(side, side, ratio=0.30, pad_ratio=0.10, min_diameter=24)
        assert geom is not None
        diameter, pad, outer = geom
        assert outer <= side
        assert diameter >= 24
        assert diameter + pad * 2 == outer
        assert (side - outer) // 2 >= 0


def test_fit_geometry_side24_respects_min_diameter():
    """side=24 时旧逻辑会缩到 diameter=20；现应 pad=0 且 diameter=24，或无法满足则 None。"""
    geom = _fit_logo_geometry(24, 24, ratio=0.30, pad_ratio=0.10, min_diameter=24)
    assert geom is not None
    diameter, pad, outer = geom
    assert diameter >= 24
    assert outer <= 24
    assert diameter + pad * 2 == outer


def test_fit_geometry_cannot_fit_min_diameter_returns_none():
    assert _fit_logo_geometry(20, 20, ratio=0.30, pad_ratio=0.10, min_diameter=24) is None
    assert _fit_logo_geometry(16, 16, ratio=0.30, pad_ratio=0.10, min_diameter=24) is None


def test_fit_geometry_tiny_image_returns_none():
    assert _fit_logo_geometry(4, 4) is None


def test_overlay_on_small_qr_skips_when_below_min(tmp_path):
    logo_path = tmp_path / "logo.png"
    logo_path.write_bytes(_logo_png())
    src = _solid_png(20)
    out = overlay_center_logo(src, logo_path=logo_path, ratio=0.30, pad_ratio=0.10)
    # 装不下 min_diameter 时原样返回
    assert out == src


def test_overlay_on_side40_keeps_min_diameter(tmp_path):
    logo_path = tmp_path / "logo.png"
    logo_path.write_bytes(_logo_png())
    out = overlay_center_logo(_solid_png(40), logo_path=logo_path, ratio=0.30, pad_ratio=0.10)
    assert out and len(out) > 50
    im = Image.open(BytesIO(out))
    assert im.size == (40, 40)


def test_overlay_on_normal_wxacode_size(tmp_path):
    logo_path = tmp_path / "logo.png"
    logo_path.write_bytes(_logo_png(128))
    out = overlay_center_logo(_solid_png(280), logo_path=logo_path, ratio=0.30)
    im = Image.open(BytesIO(out))
    assert im.size == (280, 280)
