/**
 * 环境配置
 * ─────────────────────────────────────────
 * 本地开发  Windows 11 + 微信开发者工具
 * 生产部署  Ubuntu 22.04.5 + HTTPS 域名
 * ─────────────────────────────────────────
 * 改 IP：只改 DEV_LAN_HOST
 * 发正式版：改 PROD_API_BASE 为真实域名
 */

/** 生产 API（Ubuntu 服务器，须 HTTPS，与小程序后台合法域名一致） */
const PROD_API_BASE = 'https://your-domain.com/api/v1'

/** 本地模拟器（Windows 开发者工具） */
const DEV_SIMULATOR_API = 'http://127.0.0.1:8000/api/v1'

/** 本地真机调试：电脑局域网 IP + DEV_USE_LAN=true + 后端 uvicorn --host 0.0.0.0 */
const DEV_LAN_HOST = '192.168.1.110'
const DEV_USE_LAN = false
const DEV_LAN_API = `http://${DEV_LAN_HOST}:8000/api/v1`

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

/** 模拟器用 127.0.0.1；真机自动用 DEV_LAN_HOST（手机无法访问电脑 localhost） */
function resolveDevApiBase() {
  if (DEV_USE_LAN || !isDevtools()) return DEV_LAN_API
  return DEV_SIMULATOR_API
}

/** 按运行环境选择 API 根地址 */
function resolveApiBase() {
  if (miniEnvVersion() === 'release') return PROD_API_BASE
  return resolveDevApiBase()
}

/**
 * 微信隐私接口开关（getPhoneNumber / chooseAvatar / input type=nickname 等）
 * 正式版须在公众平台声明「昵称、头像、手机号」后改为 true。
 * 开发版 / 体验版（含真机调试）默认开启昵称与头像，便于联调。
 */
const ENABLE_WX_PRIVACY_APIS = false

function resolveNicknameAvatarApisEnabled() {
  if (ENABLE_WX_PRIVACY_APIS) return true
  const ver = miniEnvVersion()
  if (ver === 'develop' || ver === 'trial') return true
  if (isDevtools()) return true
  return false
}

const enableNicknameAvatarApis = resolveNicknameAvatarApisEnabled()

module.exports = {
  resolveApiBase,
  PROD_API_BASE,
  DEV_SIMULATOR_API,
  DEV_LAN_HOST,
  DEV_USE_LAN,
  DEV_LAN_API,
  enableWxPrivacyApis: ENABLE_WX_PRIVACY_APIS,
  enableWxPhoneBind: ENABLE_WX_PRIVACY_APIS,
  enableChooseAvatar: enableNicknameAvatarApis,
  enableNicknameInput: enableNicknameAvatarApis
}
