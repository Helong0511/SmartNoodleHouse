/**
 * 微信小程序环境配置
 * ─────────────────────────────────────────────────────────────
 * 【调试 / 体验】develop | trial | 开发者工具
 *   - API 自动走 DEV_SIMULATOR_API 或 DEV_LAN_API（真机无法访问 127.0.0.1）
 *   - 隐私接口在 develop/trial/模拟器下自动开启（无需改 ENABLE_WX_PRIVACY_APIS）
 *   - 开发者工具可勾选「不校验合法域名」；project.config.json 中 urlCheck 可为 false
 *
 * 【正式版】release
 *   1. PROD_API_BASE → 真实 HTTPS 域名（与 mp.weixin.qq.com 服务器域名一致）
 *   2. ENABLE_WX_PRIVACY_APIS → true（且公众平台已配置《用户隐私保护指引》）
 *   3. 公众平台：非个人主体、已认证；勾选昵称头像、手机号；开通 URL Link / 明文 Scheme（桌台码）
 *   4. 上传前将 project.config.json 的 urlCheck 改为 true 并验证合法域名
 *   5. 后端 .env：APP_ENV=production、MINI_PROGRAM_ENV_VERSION=release
 *
 * 官方文档：
 *   隐私指引 https://developers.weixin.qq.com/miniprogram/dev/framework/user-privacy/PrivacyAuthorize.html
 *   隐私类型   https://developers.weixin.qq.com/miniprogram/dev/framework/user-privacy/miniprogram-intro.html
 * ─────────────────────────────────────────────────────────────
 * 改局域网 IP：只改 DEV_LAN_HOST
 * 详细对照表：同目录 环境说明.txt
 */

/** 生产 API（须 HTTPS，与小程序后台 request 合法域名一致） */
const PROD_API_BASE = 'https://your-domain.com/api/v1'

/** 本地模拟器（微信开发者工具） */
const DEV_SIMULATOR_API = 'http://127.0.0.1:8000/api/v1'

/** 本地真机：电脑局域网 IP + 后端 uvicorn --host 0.0.0.0 */
const DEV_LAN_HOST = '192.168.1.110'
const DEV_USE_LAN = false
const DEV_LAN_API = `http://${DEV_LAN_HOST}:8000/api/v1`

/**
 * 体验版是否连生产 API（默认 false：体验版仍走局域网，便于 UAT）
 * 预发验收时可改为 true，并确保体验版已配好合法域名
 */
const TRIAL_USE_PROD_API = false

/**
 * 微信隐私接口总开关（getPhoneNumber / chooseAvatar / input type=nickname）
 * 发正式版前须在公众平台声明对应能力后改为 true
 */
const ENABLE_WX_PRIVACY_APIS = false

function isDevtools() {
  try {
    if (wx.getDeviceInfo) return wx.getDeviceInfo().platform === 'devtools'
    return wx.getSystemInfoSync().platform === 'devtools'
  } catch (e) {
    return false
  }
}

function miniEnvVersion() {
  try {
    const info = wx.getAccountInfoSync()
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

/** 是否展示开发提示（隐私说明里的接口地址、member 页配置提示等） */
function shouldShowDevTips() {
  return !isRelease()
}

/** 非正式版在文案后附加 API 地址，便于真机联调 */
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
  if (ver === 'release') return PROD_API_BASE
  if (ver === 'trial' && TRIAL_USE_PROD_API) return PROD_API_BASE
  return resolveDevApiBase()
}

function resolvePrivacyApisEnabled() {
  if (ENABLE_WX_PRIVACY_APIS) return true
  if (isDevelopOrTrial()) return true
  if (isDevtools()) return true
  return false
}

const enablePrivacyApis = resolvePrivacyApisEnabled()

function getEnvDebugInfo() {
  return {
    envVersion: miniEnvVersion() || 'unknown',
    isDevtools: isDevtools(),
    isRelease: isRelease(),
    apiBase: resolveApiBase(),
    privacyApis: enablePrivacyApis
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
  TRIAL_USE_PROD_API,
  enableWxPrivacyApis: ENABLE_WX_PRIVACY_APIS || enablePrivacyApis,
  enableWxPhoneBind: enablePrivacyApis,
  enableChooseAvatar: enablePrivacyApis,
  enableNicknameInput: enablePrivacyApis
}
