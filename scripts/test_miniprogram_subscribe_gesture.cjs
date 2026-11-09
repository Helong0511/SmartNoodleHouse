const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

async function main() {
  const root = path.resolve(__dirname, "..");

  // 支付成功后的异步流程不能自动弹订阅面板；必须由结果页的显式用户点击触发。
  for (const platform of ["wechat", "alipay"]) {
    const dir = platform === "wechat" ? "miniprogram-wechat" : "miniprogram-alipay";
    const confirmSource = fs.readFileSync(
      path.join(root, dir, "packageOrder/confirm/confirm.js"),
      "utf8"
    );
    const resultSource = fs.readFileSync(
      path.join(root, dir, "packageOrder/result/result.js"),
      "utf8"
    );
    assert.doesNotMatch(confirmSource, /requestOrderReadySubscribe/,
      platform + " must not request subscription automatically after checkout");
    const handlerStart = resultSource.indexOf("async subscribeOrderReady()");
    assert.notEqual(handlerStart, -1, platform + " must expose an explicit subscribe tap handler");
    const handlerEnd = resultSource.indexOf("\n  },", handlerStart);
    assert.notEqual(handlerEnd, -1, platform + " subscribe tap handler must be bounded");
    const handler = resultSource.slice(handlerStart, handlerEnd);
    const apiCall = handler.indexOf("subscribe.requestOrderReadySubscribe()");
    const firstAwait = handler.indexOf("await ");
    assert.ok(apiCall >= 0 && (firstAwait < 0 || apiCall < firstAwait),
      platform + " must invoke platform subscribe API synchronously before awaiting");
  }

  const wxConfigPath = require.resolve(path.join(root, "miniprogram-wechat/utils/clientConfig.js"));
  require.cache[wxConfigPath] = {
    id: wxConfigPath,
    filename: wxConfigPath,
    loaded: true,
    exports: {
      loadClientConfig: async () => ({
        subscribe_templates: { order_ready: "wx-order-ready", balance_low: "wx-balance-low" }
      })
    }
  };
  let wxCalled = false;
  global.wx = {
    requestSubscribeMessage(options) {
      wxCalled = true;
      assert.deepEqual(options.tmplIds, ["wx-order-ready"]);
      assert.equal(Object.prototype.hasOwnProperty.call(options, "templateIds"), false);
      options.success({ "wx-order-ready": "accept" });
    }
  };
  const wxSubscribe = require(path.join(root, "miniprogram-wechat/utils/subscribe.js"));
  await wxSubscribe.loadTemplates();
  const wxRequest = wxSubscribe.requestOrderReadySubscribe();
  assert.equal(wxCalled, true, "WeChat subscribe API must be invoked synchronously from tap handler");
  await wxRequest;

  const aliConfigPath = require.resolve(path.join(root, "miniprogram-alipay/utils/clientConfig.js"));
  require.cache[aliConfigPath] = {
    id: aliConfigPath,
    filename: aliConfigPath,
    loaded: true,
    exports: {
      loadClientConfig: async () => ({
        subscribe_templates: {
          alipay_order_ready: "ali-order-ready",
          alipay_balance_low: "ali-balance-low"
        }
      })
    }
  };
  let aliCalled = false;
  global.my = {
    requestSubscribeMessage(options) {
      aliCalled = true;
      assert.deepEqual(options.entityIds, ["ali-order-ready"]);
      assert.equal(Object.prototype.hasOwnProperty.call(options, "templateIds"), false);
      options.success({ "ali-order-ready": "ACCEPT" });
    }
  };
  const aliSubscribe = require(path.join(root, "miniprogram-alipay/utils/subscribe.js"));
  await aliSubscribe.loadTemplates();
  const aliRequest = aliSubscribe.requestOrderReadySubscribe();
  assert.equal(aliCalled, true, "Alipay subscribe API must be invoked synchronously from tap handler");
  await aliRequest;

  console.log("Mini-program subscribe gesture checks passed.");
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
