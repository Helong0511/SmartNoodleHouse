/**
 * 支付宝小程序环境配置
 * ─────────────────────────────────────────────────────────────
 * 【调试 / 体验】develop | trial | 开发者工具
 *   - API 自动走 DEV_SIMULATOR_API 或 DEV_LAN_API
 *   - 隐私接口在 develop/trial/模拟器下自动开启
 *   - 真机预览隐私弹窗（开发者工具不支持）；IDE「联调设置」开启
 *
 * 【正式版】release
 *   1. PROD_API_BASE → 真实 HTTPS 域名（与开放平台服务器域名白名单一致）
 *   2. ENABLE_ALIPAY_PRIVACY_APIS → true（且开放平台已配置「小程序隐私政策」）
 *   3. 开放平台：申请「获取会员手机号」、配置接口内容加密 AES → 后端 ALIPAY_AES_KEY
 *   4. 头像昵称用 chooseAvatar + input type=nickname（勿用已废弃 my.getOpenUserInfo）
 *   5. 支付宝隐私按钮不能 pipe 组合：须先 agreePrivacyAuthorization，再 getAuthorize
 *   6. 后端 .env：APP_ENV=production、ALIPAY_AES_KEY、ALIPAY_APP_ID 等
 *
 * 官方文档：
 *   隐私规则 https://opendocs.alipay.com/common/0fc513
 *   手机号   https://opendocs.alipay.com/mini/api/getphonenumber
 *   隐私授权 https://opendocs.alipay.com/mini/0b1o5r
 * ─────────────────────────────────────────────────────────────
 * 改局域网 IP：只改 DEV_LAN_HOST
 * 详细对照表：同目录 环境说明.txt
 */

/** 生产 API（须 HTTPS，与开放平台服务器域名白名单一致） */
const PROD_API_BASE = 'https://your-domain.com/api/v1'

/** 本地模拟器（支付宝开发者工具） */
const DEV_SIMULATOR_API = 'http://127.0.0.1:8000/api/v1'

/** 本地真机：电脑局域网 IP + 后端 uvicorn --host 0.0.0.0 */
const DEV_LAN_HOST = '192.168.1.110'
const DEV_USE_LAN = false
const DEV_LAN_API = `http://${DEV_LAN_HOST}:8000/api/v1`

/** 体验版是否连生产 API（默认 false） */
const TRIAL_USE_PROD_API = false

/**
 * 支付宝隐私接口总开关
 * 发正式版前须在开放平台配置「小程序隐私政策」后改为 true
 */
const ENABLE_ALIPAY_PRIVACY_APIS = false

function isDevtools() {
  try {
    const sys = my.getSystemInfoSync()
    return sys.platform === 'devtools' || /devtools/i.test(sys.model || '')
  } catch (e) {
    return false
  }
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
  return !isRelease()
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
  if (ver === 'release') return PROD_API_BASE
  if (ver === 'trial' && TRIAL_USE_PROD_API) return PROD_API_BASE
  return resolveDevApiBase()
}

function resolvePrivacyApisEnabled() {
  if (ENABLE_ALIPAY_PRIVACY_APIS) return true
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
  enableAlipayPrivacyApis: ENABLE_ALIPAY_PRIVACY_APIS || enablePrivacyApis,
  enableChooseAvatar: enablePrivacyApis,
  enableNicknameInput: enablePrivacyApis,
  enableAlipayPhoneBind: enablePrivacyApis,
  /** @deprecated 请用 enableChooseAvatar */
  enableAlipayUserProfile: enablePrivacyApis
}
