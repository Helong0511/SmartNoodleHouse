/** 从后端下发的小程序公开配置（GET /miniprogram/config?store_id=） */

const api = require('./api')

let _cache = null
let _cacheStoreId = null
let _loading = null
let _loadingStoreId = null

const DEFAULTS = {
  enable_alipay_avatar: true,
  enable_alipay_nickname: true,
  enable_alipay_phone_bind: false,
  enable_wx_avatar: true,
  enable_wx_nickname: true,
  enable_wx_phone_bind: false,
  enable_face_welcome: false,
  show_menu_search: false,
  show_menu_smart_taste: false,
  show_menu_guess_you_like: false,
  hide_member_empty_wallet: true,
  enable_customer_display: false
}

function getAppSafe() {
  try {
    return getApp()
  } catch (_) {
    return null
  }
}

function resolveStoreId(explicit) {
  if (explicit != null && explicit !== '') {
    const n = Number(explicit)
    return Number.isFinite(n) && n > 0 ? n : null
  }
  try {
    const auth = require('./auth')
    if (typeof auth.currentStoreId === 'function') {
      const sid = auth.currentStoreId()
      const n = Number(sid)
      return Number.isFinite(n) && n > 0 ? n : null
    }
  } catch (_) {
    /* ignore */
  }
  const app = getAppSafe()
  const gd = app && app.globalData
  if (!gd) return null
  const store = gd.store || {}
  const table = gd.table || {}
  const sid = store.id || store.store_id || table.store_id || null
  const n = Number(sid)
  return Number.isFinite(n) && n > 0 ? n : null
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

function applyCacheToApp(cache) {
  const app = getAppSafe()
  if (!app || !app.globalData) return
  app.globalData.clientConfig = cache
  app.globalData.enableAlipayAvatar = !!cache.enable_alipay_avatar
  app.globalData.enableAlipayNickname = !!cache.enable_alipay_nickname
  app.globalData.enableAlipayPhoneBind = !!cache.enable_alipay_phone_bind
  app.globalData.enableWxAvatar = !!cache.enable_wx_avatar
  app.globalData.enableWxNickname = !!cache.enable_wx_nickname
  app.globalData.enableWxPhoneBind = !!cache.enable_wx_phone_bind
  app.globalData.showMenuSearch = !!cache.show_menu_search
  app.globalData.showMenuSmartTaste = !!cache.show_menu_smart_taste
  app.globalData.showMenuGuessYouLike = !!cache.show_menu_guess_you_like
  app.globalData.hideMemberEmptyWallet = !!cache.hide_member_empty_wallet
  app.globalData.enableCustomerDisplay = !!cache.enable_customer_display
  app.globalData.enableFaceWelcome = !!cache.enable_face_welcome
}

/**
 * @param {boolean} [force=false]
 * @param {number|null} [storeId=null] 显式门店；默认从当前桌台/门店上下文解析
 */
async function loadClientConfig(force = false, storeId = null) {
  const sid = resolveStoreId(storeId)
  if (!force && _cache && _cacheStoreId === sid) return _cache
  if (!force && _loading && _loadingStoreId === sid) return _loading
  _loadingStoreId = sid
  _loading = (async () => {
    try {
      const params = sid ? { store_id: sid } : {}
      const data = await api.get('/miniprogram/config', params, false)
      _cache = data && typeof data === 'object' ? { ...DEFAULTS, ...data } : { ...DEFAULTS }
      _cacheStoreId = sid
    } catch (_) {
      _cache = _cache || { ...DEFAULTS }
    } finally {
      _loading = null
      _loadingStoreId = null
    }
    applyCacheToApp(_cache)
    return _cache
  })()
  return _loading
}

/** 切店后丢弃按门店缓存的配置 */
function invalidateClientConfig() {
  _cache = null
  _cacheStoreId = null
  _loading = null
  _loadingStoreId = null
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

function isFaceWelcomeEnabled() {
  return boolFlag('enable_face_welcome', DEFAULTS.enable_face_welcome)
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
    hideMemberEmptyWallet: !!isHideMemberEmptyWalletEnabled(),
    showFaceWelcome: !!isFaceWelcomeEnabled()
  }
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
  invalidateClientConfig,
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
  isFaceWelcomeEnabled,
  menuPageFlags,
  memberPageFlags,
  alipayPrivacyPageData,
  wxPrivacyPageData
}
