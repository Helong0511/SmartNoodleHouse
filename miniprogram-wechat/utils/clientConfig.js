/** 从后端下发的小程序公开配置（GET /miniprogram/config） */

const api = require('./api')

let _cache = null
let _loading = null

const DEFAULTS = {
  enable_alipay_avatar: true,
  enable_alipay_nickname: true,
  enable_alipay_phone_bind: false,
  enable_wx_avatar: true,
  enable_wx_nickname: true,
  enable_wx_phone_bind: false,
  show_menu_search: true,
  show_menu_smart_taste: true,
  show_menu_guess_you_like: true,
  hide_member_empty_wallet: true
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
      app.globalData.showMenuSearch = !!_cache.show_menu_search
      app.globalData.showMenuSmartTaste = !!_cache.show_menu_smart_taste
      app.globalData.showMenuGuessYouLike = !!_cache.show_menu_guess_you_like
      app.globalData.hideMemberEmptyWallet = !!_cache.hide_member_empty_wallet
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

function isMenuSearchEnabled() {
  return boolFlag('show_menu_search', DEFAULTS.show_menu_search)
}

function isMenuSmartTasteEnabled() {
  return boolFlag('show_menu_smart_taste', DEFAULTS.show_menu_smart_taste)
}

function isMenuGuessYouLikeEnabled() {
  return boolFlag('show_menu_guess_you_like', DEFAULTS.show_menu_guess_you_like)
}

function isHideMemberEmptyWalletEnabled() {
  return boolFlag('hide_member_empty_wallet', DEFAULTS.hide_member_empty_wallet)
}

/** 点餐页展示开关对应的 setData 字段 */
function menuPageFlags() {
  return {
    showMenuSearch: !!isMenuSearchEnabled(),
    showMenuSmartTaste: !!isMenuSmartTasteEnabled(),
    showMenuGuessYouLike: !!isMenuGuessYouLikeEnabled()
  }
}

/** 会员中心展示开关对应的 setData 字段 */
function memberPageFlags() {
  return {
    hideMemberEmptyWallet: !!isHideMemberEmptyWalletEnabled()
  }
}

function alipayPrivacyPageData() {
  return {
    canChooseAvatar: !!isAlipayAvatarEnabled(),
    useAlipayNickname: !!isAlipayNicknameEnabled(),
    canAlipayPhone: !!isAlipayPhoneBindEnabled()
  }
}

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
  isMenuSearchEnabled,
  isMenuSmartTasteEnabled,
  isMenuGuessYouLikeEnabled,
  isHideMemberEmptyWalletEnabled,
  menuPageFlags,
  memberPageFlags,
  alipayPrivacyPageData,
  wxPrivacyPageData
}
