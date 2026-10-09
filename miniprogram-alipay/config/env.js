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

/** 本地真机：电脑局域网 IP + 后端 uvicorn --host 0.0.0.0 */
const DEV_LAN_HOST = '192.168.1.110'
const DEV_USE_LAN = false
const DEV_LAN_API = `http://${DEV_LAN_HOST}:8000/api`

/** 体验版是否连生产 API（默认 false） */
const TRIAL_USE_PROD_API = false

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
    return (info && info.miniProgram && info.miniProgram.envVersion) || ''
  } catch (e) {
    return ''
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
  if (DEV_USE_LAN || !isDevtools()) return DEV_LAN_API
  return DEV_SIMULATOR_API
}

function resolveApiBase() {
  const ver = miniEnvVersion()
  if (ver === 'release' || (ver === 'trial' && TRIAL_USE_PROD_API)) {
    // P0-4：正式版强制校验 HTTPS 且拒绝占位符
    if (PROD_API_BASE.indexOf('your-domain.com') !== -1) {
      throw new Error('PROD_API_BASE 仍为占位符，请在 env.js 替换为真实域名后再上传正式版')
    }
    if (!/^https:\/\//i.test(PROD_API_BASE)) {
      throw new Error('PROD_API_BASE 必须为 HTTPS 地址')
    }
    return PROD_API_BASE
  }
  return resolveDevApiBase()
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
