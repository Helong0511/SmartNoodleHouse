#!/usr/bin/env node
"use strict";

// 用 Node 模拟平台环境探测失败，确保正式环境不会回退到 HTTP 局域网 API。
const assert = require("node:assert/strict");
const path = require("node:path");

async function main() {
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

function loadWithVersion(relativePath, globalName, version) {
  global[globalName] = {
    getAccountInfoSync() {
      return { miniProgram: { envVersion: version } };
    },
    getSystemInfoSync() {
      return { platform: "android", model: "test-device" };
    }
  };
  const resolved = path.resolve(__dirname, "..", relativePath);
  delete require.cache[require.resolve(resolved)];
  return require(resolved);
}

// 上传体验版必须走 HTTPS API；未知/灰度环境必须按正式环境处理。
for (const [relativePath, globalName] of [
  ["miniprogram-wechat/config/env.js", "wx"],
  ["miniprogram-alipay/config/env.js", "my"]
]) {
  const trial = loadWithVersion(relativePath, globalName, "trial");
  assert.equal(trial.isRelease(), false, relativePath + " trial remains a test version");
  assert.equal(trial.resolveApiBase(), trial.PROD_API_BASE, relativePath + " trial uses HTTPS API");
  assert.throws(
    () => trial.assertApiBaseConfigured(trial.PROD_API_BASE),
    /正式版 API 地址未正确配置/,
    relativePath + " trial rejects placeholder API"
  );

  const unknown = loadWithVersion(relativePath, globalName, "gray");
  assert.equal(unknown.isRelease(), true, relativePath + " gray/unknown fails closed");
  assert.equal(unknown.resolveApiBase(), unknown.PROD_API_BASE, relativePath + " gray uses production API");
}


// 配置校验失败必须以 rejected Promise 返回，不能在 api.get() 调用点同步抛出。
for (const [relativePath, globalName, apiPath] of [
  ["miniprogram-wechat/config/env.js", "wx", "miniprogram-wechat/utils/api.js"],
  ["miniprogram-alipay/config/env.js", "my", "miniprogram-alipay/utils/api.js"]
]) {
  loadWithFailingEnvironment(relativePath, globalName);
  const resolvedApi = path.resolve(__dirname, "..", apiPath);
  delete require.cache[require.resolve(resolvedApi)];
  const api = require(resolvedApi);
  await assert.rejects(api.get("/health"), (err) =>
    err && err.code === "API_BASE_INVALID" && /正式版 API 地址未正确配置/.test(err.message),
    apiPath + " must reject invalid API base asynchronously"
  );
}

console.log("Mini-program environment fail-closed checks passed.");
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
