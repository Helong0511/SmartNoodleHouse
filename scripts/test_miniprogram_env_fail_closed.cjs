#!/usr/bin/env node
"use strict";

// 用 Node 模拟平台环境探测失败，确保正式环境不会回退到 HTTP 局域网 API。
const assert = require("node:assert/strict");
const path = require("node:path");

function loadWithFailingEnvironment(relativePath, globalName) {
  global[globalName] = {
    getAccountInfoSync() {
      throw new Error("platform API unavailable");
    },
    getSystemInfoSync() {
      return { platform: "android", model: "test-device" };
    }
  };
  const resolved = path.resolve(__dirname, "..", relativePath);
  delete require.cache[require.resolve(resolved)];
  return require(resolved);
}

for (const [relativePath, globalName] of [
  ["miniprogram-wechat/config/env.js", "wx"],
  ["miniprogram-alipay/config/env.js", "my"]
]) {
  const env = loadWithFailingEnvironment(relativePath, globalName);
  assert.equal(env.isRelease(), true, relativePath);
  assert.equal(env.resolveApiBase(), env.PROD_API_BASE, relativePath);
  assert.throws(
    () => env.assertApiBaseConfigured(env.PROD_API_BASE),
    /正式版 API 地址未正确配置/,
    relativePath
  );
}
console.log("Mini-program environment fail-closed checks passed.");
