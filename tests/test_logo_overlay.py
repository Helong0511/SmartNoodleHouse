"""logo_overlay：小图也不应出现负偏移。"""
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


def test_fit_geometry_never_exceeds_side():
    for side in (16, 24, 32, 48, 64, 120, 280):
        geom = _fit_logo_geometry(side, side, ratio=0.30, pad_ratio=0.10, min_diameter=24)
        assert geom is not None
        diameter, pad, outer = geom
        assert outer <= side
        assert diameter + pad * 2 == outer
        assert (side - outer) // 2 >= 0


def test_fit_geometry_tiny_image_returns_none():
    assert _fit_logo_geometry(4, 4) is None


def test_overlay_on_small_qr_does_not_raise(tmp_path):
    logo_path = tmp_path / "logo.png"
    logo_path.write_bytes(_logo_png())
    # 小于 min_diameter*外扩 的图：旧逻辑会 outer>side 产生负偏移
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
