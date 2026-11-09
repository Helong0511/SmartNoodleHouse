#!/usr/bin/env node
"use strict";

const assert = require("node:assert/strict");

// 模拟支付宝基础库缺少隐私 API：必须要求自有隐私授权，而不是默认为已同意。
global.my = {
  getStorageSync() { return { data: null }; }
};
const privacyPath = require.resolve("../miniprogram-alipay/utils/privacy.js");
delete require.cache[privacyPath];
const privacy = require(privacyPath);

(async () => {
  const status = await privacy.setupPrivacy();
  assert.equal(status.needAuthorization, true);
  assert.equal(status.unsupported, true);

  global.my.getStorageSync = () => ({ data: Date.now() });
  assert.equal(privacy.hasPrivacyAgreed(), true);
  assert.equal(await privacy.ensurePrivacyConsent(), true);
  console.log("Alipay mini-program privacy fail-closed checks passed.");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
