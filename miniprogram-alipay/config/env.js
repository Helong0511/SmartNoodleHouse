/**
 * 支付宝小程序环境配置
 * ─────────────────────────────────────────────────────────────
 * 【调试 / 体验】develop | trial | 开发者工具
 *   - API：DEV_SIMULATOR_API / DEV_LAN_API
 *   - 头像 / 昵称 / 手机号开关在项目根 .env：
 *     ENABLE_ALIPAY_AVATAR / ENABLE_ALIPAY_NICKNAME / ENABLE_ALIPAY_PHONE_BIND
 *   - 点餐页搜索 / 智能口味 / 猜你喜欢：SHOW_MENU_*（或后台「门店 → 门店信息 → 业务参数」）
 *     （经后端 GET /api/miniprogram/config 下发；后台热更无需重启）
 *
 * 【正式版】release
 *   1. PROD_API_BASE → HTTPS 域名
 *   2. .env 按能力打开三项开关；手机号须企业主体已开通「获取会员手机号」
 * ─────────────────────────────────────────────────────────────
 * 改局域网 IP：只改 DEV_LAN_HOST
 * 详细对照表：同目录 环境说明.txt
 */

/** 生产 API（须 HTTPS，与开放平台服务器域名白名单一致） */
// TODO(发布前必改)：替换为真实 HTTPS 域名，并在 open.alipay.com 配置服务器域名白名单。
// 保持占位符时 release 版本会全部请求失败，请勿直接上传正式版。
const PROD_API_BASE = 'https://your-domain.com/api'

/** 本地模拟器（支付宝开发者工具） */
const DEV_SIMULATOR_API = 'http://127.0.0.1:8000/api'

/** 本地真机：仅开发版显式开启 DEV_USE_LAN 后使用电脑局域网 IP */
const DEV_LAN_HOST = '192.168.1.110'
const DEV_USE_LAN = false
const DEV_LAN_API = `http://${DEV_LAN_HOST}:8000/api`

/** 体验版默认使用生产 HTTPS API；禁止上传版回退到内网 HTTP */
const TRIAL_USE_PROD_API = true

function isDevtools() {
  try {
    // 支付宝 IDE 部分版本 platform 会伪装成 iOS/Android，不能只看 platform
    if (typeof my !== 'undefined' && my.isIDE === true) return true
  } catch (_) { }
  try {
    const sys = my.getSystemInfoSync() || {}
    const blob = [sys.platform, sys.model, sys.brand, sys.system].filter(Boolean).join(' ')
    if (/devtools|simulator|模拟器/i.test(blob)) return true
    // 无真机标识时，开发者工具里常见的桌面环境
    if (!sys.platform && !sys.model) return true
  } catch (_) { }
  return false
}

function miniEnvVersion() {
  try {
    const info = my.getAccountInfoSync && my.getAccountInfoSync()
    const version = String((info && info.miniProgram && info.miniProgram.envVersion) || '').toLowerCase()
    // 只信任平台明确返回的 develop/trial；探测异常、空值或未知值均按正式版处理。
    return ['develop', 'trial', 'release'].includes(version) ? version : 'release'
  } catch (e) {
    return 'release'
  }
}

function isRelease() {
  return miniEnvVersion() === 'release'
}

function isDevelopOrTrial() {
  const ver = miniEnvVersion()
  return ver === 'develop' || ver === 'trial'
}

function shouldShowDevTips() {
  // 仅开发版显示调试入口；体验版（审核场景）与正式版一律隐藏，符合平台
  // 「小程序域内页面不得有开发调试代码入口」的准入要求
  return miniEnvVersion() === 'develop'
}

function appendDevApiHint(text, apiBase) {
  const msg = String(text || '').trim()
  if (isRelease()) return msg
  const base = String(apiBase || resolveApiBase() || '').trim()
  return base ? `${msg} 接口：${base}` : msg
}

function resolveDevApiBase() {
  if (isDevtools()) return DEV_SIMULATOR_API
  // 真机只有显式开启 DEV_USE_LAN 才走局域网；false 时绝不偷偷回退到内网 HTTP。
  return DEV_USE_LAN ? DEV_LAN_API : DEV_SIMULATOR_API
}

function resolveApiBase() {
  const ver = miniEnvVersion()
  // 只有平台明确标记的开发版，或明确选择本地 API 的体验版，才允许使用 HTTP 开发地址。
  if (ver === 'develop' || (ver === 'trial' && !TRIAL_USE_PROD_API)) {
    return resolveDevApiBase()
  }
  // App 模块初始化时也会调用此函数；不要在这里抛错导致正式版白屏。
  // 首个真实 API 请求前由 assertApiBaseConfigured() 完成严格校验。
  return PROD_API_BASE
}

function assertApiBaseConfigured(apiBase) {
  const ver = miniEnvVersion()
  if (ver === 'develop' || (ver === 'trial' && !TRIAL_USE_PROD_API)) return
  const base = String(apiBase || PROD_API_BASE).trim()
  if (!/^https:\/\//i.test(base) || /your-domain\.com/i.test(base)) {
    throw new Error('正式版 API 地址未正确配置：请在 config/env.js 设置真实 HTTPS 域名并加入平台合法域名')
  }
}

function getEnvDebugInfo() {
  return {
    envVersion: miniEnvVersion() || 'unknown',
    isDevtools: isDevtools(),
    isRelease: isRelease(),
    apiBase: resolveApiBase()
  }
}

module.exports = {
  resolveApiBase,
  assertApiBaseConfigured,
  getEnvDebugInfo,
  isRelease,
  isDevelopOrTrial,
  shouldShowDevTips,
  appendDevApiHint,
  PROD_API_BASE,
  DEV_SIMULATOR_API,
  DEV_LAN_HOST,
  DEV_USE_LAN,
  DEV_LAN_API,
  TRIAL_USE_PROD_API
}
