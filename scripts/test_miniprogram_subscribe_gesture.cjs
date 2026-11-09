const assert = require("node:assert/strict");
const path = require("node:path");

async function main() {
  const root = path.resolve(__dirname, "..");

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
