"""微信/支付宝小程序共享代码一致性检查。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 两套小程序需要分别打包；这些共享逻辑文件必须保持字节级一致，避免双端修复漂移。
SHARED_PAIRS = (
    ("miniprogram-wechat/utils/checkoutPricing.js", "miniprogram-alipay/utils/checkoutPricing.js"),
    ("miniprogram-wechat/utils/clientConfig.js", "miniprogram-alipay/utils/clientConfig.js"),
    ("miniprogram-wechat/utils/date.js", "miniprogram-alipay/utils/date.js"),
    ("miniprogram-wechat/utils/pickup.js", "miniprogram-alipay/utils/pickup.js"),
    ("miniprogram-wechat/utils/sharedCart.js", "miniprogram-alipay/utils/sharedCart.js"),
    ("miniprogram-wechat/utils/taxNo.js", "miniprogram-alipay/utils/taxNo.js"),
    ("miniprogram-wechat/pages/confirm/confirm.js", "miniprogram-alipay/pages/confirm/confirm.js"),
    ("miniprogram-wechat/pages/product/product.js", "miniprogram-alipay/pages/product/product.js"),
    ("miniprogram-wechat/pages/result/result.js", "miniprogram-alipay/pages/result/result.js"),
    ("miniprogram-wechat/components/privacy-popup/privacy-popup.json", "miniprogram-alipay/components/privacy-popup/privacy-popup.json"),
    ("miniprogram-wechat/components/skeleton/skeleton.json", "miniprogram-alipay/components/skeleton/skeleton.json"),
    ("miniprogram-wechat/components/store-bar/store-bar.json", "miniprogram-alipay/components/store-bar/store-bar.json"),
    ("miniprogram-wechat/components/recommend-strip/recommend-strip.json", "miniprogram-alipay/components/recommend-strip/recommend-strip.json"),
    ("miniprogram-wechat/assets/logo.png", "miniprogram-alipay/assets/logo.png"),
)


def test_shared_wechat_alipay_files_do_not_drift():
    for wechat_path, alipay_path in SHARED_PAIRS:
        wechat_file = ROOT / wechat_path
        alipay_file = ROOT / alipay_path
        assert wechat_file.is_file(), f"缺少微信端共享文件：{wechat_path}"
        assert alipay_file.is_file(), f"缺少支付宝端共享文件：{alipay_path}"
        assert wechat_file.read_bytes() == alipay_file.read_bytes(), (
            f"微信/支付宝共享文件内容不一致：{wechat_path} != {alipay_path}；"
            "修改共享逻辑时请同步两端，或抽取为明确的共享源码生成流程。"
        )


def test_shared_cart_clients_do_not_send_client_calculated_unit_price():
    for path in (
        ROOT / "miniprogram-wechat/utils/sharedCart.js",
        ROOT / "miniprogram-alipay/utils/sharedCart.js",
    ):
        source = path.read_text(encoding="utf-8")
        assert "unit_price:" not in source
