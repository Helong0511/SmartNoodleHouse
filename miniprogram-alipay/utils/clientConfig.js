/** 从后端 .env 下发的小程序公开配置（GET /miniprogram/config） */

const api = require('./api')

let _cache = null
let _loading = null

const DEFAULTS = {
  enable_alipay_avatar: true,
  enable_alipay_nickname: true,
  enable_alipay_phone_bind: false,
  enable_wx_avatar: true,
  enable_wx_nickname: true,
  enable_wx_phone_bind: false
}

function getAppSafe() {
  try {
    return getApp()
  } catch (_) {
    return null
  }
}

function boolFlag(key, fallback) {
  if (_cache && typeof _cache[key] === 'boolean') return _cache[key]
  const app = getAppSafe()
  const gd = app && app.globalData
  if (gd && gd.clientConfig && typeof gd.clientConfig[key] === 'boolean') {
    return gd.clientConfig[key]
  }
  return fallback
}

async function loadClientConfig(force = false) {
  if (!force && _cache) return _cache
  if (!force && _loading) return _loading
  _loading = (async () => {
    try {
      const data = await api.get('/miniprogram/config', {}, false)
      _cache = data && typeof data === 'object' ? { ...DEFAULTS, ...data } : { ...DEFAULTS }
    } catch (_) {
      _cache = _cache || { ...DEFAULTS }
    } finally {
      _loading = null
    }
    const app = getAppSafe()
    if (app && app.globalData) {
      app.globalData.clientConfig = _cache
      app.globalData.enableAlipayAvatar = !!_cache.enable_alipay_avatar
      app.globalData.enableAlipayNickname = !!_cache.enable_alipay_nickname
      app.globalData.enableAlipayPhoneBind = !!_cache.enable_alipay_phone_bind
      app.globalData.enableWxAvatar = !!_cache.enable_wx_avatar
      app.globalData.enableWxNickname = !!_cache.enable_wx_nickname
      app.globalData.enableWxPhoneBind = !!_cache.enable_wx_phone_bind
    }
    return _cache
  })()
  return _loading
}

function isAlipayAvatarEnabled() {
  return boolFlag('enable_alipay_avatar', DEFAULTS.enable_alipay_avatar)
}

function isAlipayNicknameEnabled() {
  return boolFlag('enable_alipay_nickname', DEFAULTS.enable_alipay_nickname)
}

function isAlipayPhoneBindEnabled() {
  return boolFlag('enable_alipay_phone_bind', DEFAULTS.enable_alipay_phone_bind)
}

function isWxAvatarEnabled() {
  return boolFlag('enable_wx_avatar', DEFAULTS.enable_wx_avatar)
}

function isWxNicknameEnabled() {
  return boolFlag('enable_wx_nickname', DEFAULTS.enable_wx_nickname)
}

function isWxPhoneBindEnabled() {
  return boolFlag('enable_wx_phone_bind', DEFAULTS.enable_wx_phone_bind)
}

/** 支付宝页：头像 / 昵称 / 手机号三个开关对应的 setData 字段 */
function alipayPrivacyPageData() {
  return {
    canChooseAvatar: !!isAlipayAvatarEnabled(),
    useAlipayNickname: !!isAlipayNicknameEnabled(),
    canAlipayPhone: !!isAlipayPhoneBindEnabled()
  }
}

/** 微信页：头像 / 昵称 / 手机号三个开关对应的 setData 字段 */
function wxPrivacyPageData() {
  return {
    canChooseAvatar: !!isWxAvatarEnabled(),
    useWxNickname: !!isWxNicknameEnabled(),
    canWxPhone: !!isWxPhoneBindEnabled()
  }
}

module.exports = {
  loadClientConfig,
  isAlipayAvatarEnabled,
  isAlipayNicknameEnabled,
  isAlipayPhoneBindEnabled,
  isWxAvatarEnabled,
  isWxNicknameEnabled,
  isWxPhoneBindEnabled,
  alipayPrivacyPageData,
  wxPrivacyPageData
}
