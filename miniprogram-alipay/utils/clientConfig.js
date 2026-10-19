/** 从后端下发的小程序公开配置（GET /miniprogram/config?store_id=） */

const api = require('./api')

let _cache = null
let _cacheStoreId = null
let _cacheAt = 0
let _loading = null
let _loadingStoreId = null
let _loadGen = 0
let _softProbe = null
let _softProbeStoreId = null
let _softProbeAt = 0

/** 门店业务开关（公益/营销等）可随时改；TTL 内靠 config_version 静默探测 */
const CACHE_TTL_MS = 15 * 1000
/** 版本探测最短间隔，避免 Tab 连点打满 config */
const SOFT_PROBE_COOLDOWN_MS = 3 * 1000

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
  enable_social_benefit: true,
  enable_marketing_campaigns: true,
  enable_customer_display: false,
  invoice_enabled: false,
  shared_cart_enabled: false,
  config_version: 0
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
  app.globalData.configVersion = Number((cache && cache.config_version) || 0) || 0
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
  app.globalData.enableSocialBenefit = !!cache.enable_social_benefit
  app.globalData.enableMarketingCampaigns = !!cache.enable_marketing_campaigns
  app.globalData.enableCustomerDisplay = !!cache.enable_customer_display
  app.globalData.enableFaceWelcome = !!cache.enable_face_welcome
}

function applyFetched(data, sid, gen) {
  if (gen !== _loadGen) return _cache
  _cache = data && typeof data === 'object' ? { ...DEFAULTS, ...data } : { ...DEFAULTS }
  _cacheStoreId = sid
  _cacheAt = Date.now()
  applyCacheToApp(_cache)
  return _cache
}

/** TTL 命中时探测 config_version；有变更则立刻替换缓存。返回 Promise 供调用方等齐再 apply UI。 */
function softProbeConfig(sid) {
  if (_softProbe && _softProbeStoreId === sid) return _softProbe
  const now = Date.now()
  if (_softProbeAt && now - _softProbeAt < SOFT_PROBE_COOLDOWN_MS && _cache && _cacheStoreId === sid) {
    return Promise.resolve(_cache)
  }
  const known = Number((_cache && _cache.config_version) || 0) || 0
  _softProbeStoreId = sid
  _softProbeAt = now
  _softProbe = (async () => {
    try {
      const params = sid ? { store_id: sid } : {}
      const data = await api.get('/miniprogram/config', params, false)
      const ver = Number((data && data.config_version) || 0) || 0
      if (!_cache || _cacheStoreId !== sid) {
        applyFetched(data, sid, _loadGen)
        return _cache
      }
      if (ver !== known) {
        applyFetched(data, sid, _loadGen)
      } else {
        _cacheAt = Date.now()
      }
      return _cache
    } catch (_) {
      return _cache
    } finally {
      _softProbe = null
      _softProbeStoreId = null
    }
  })()
  return _softProbe
}

/**
 * @param {boolean} [force=false]
 * @param {number|null} [storeId=null] 显式门店；默认从当前桌台/门店上下文解析
 */
async function loadClientConfig(force = false, storeId = null) {
  const sid = resolveStoreId(storeId)
  const now = Date.now()
  const fresh = _cache && _cacheStoreId === sid && _cacheAt && now - _cacheAt < CACHE_TTL_MS
  if (!force && fresh) {
    return softProbeConfig(sid).then((cfg) => cfg || _cache)
  }
  if (!force && _loading && _loadingStoreId === sid) return _loading
  const gen = ++_loadGen
  _loadingStoreId = sid
  _loading = (async () => {
    try {
      const params = sid ? { store_id: sid } : {}
      const data = await api.get('/miniprogram/config', params, false)
      return applyFetched(data, sid, gen) || _cache
    } catch (_) {
      if (gen === _loadGen) {
        _cache = _cache || { ...DEFAULTS }
        if (!_cacheAt) _cacheAt = Date.now()
        applyCacheToApp(_cache)
      }
      return _cache
    } finally {
      if (gen === _loadGen) {
        _loading = null
        _loadingStoreId = null
      }
    }
  })()
  return _loading
}

/** 切店后丢弃按门店缓存的配置 */
function invalidateClientConfig() {
  _cache = null
  _cacheStoreId = null
  _cacheAt = 0
  _loading = null
  _loadingStoreId = null
  _softProbe = null
  _softProbeStoreId = null
  _softProbeAt = 0
  _loadGen += 1
}

function currentConfigVersion() {
  return Number((_cache && _cache.config_version) || 0) || 0
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

function isSocialBenefitEnabled() {
  return boolFlag('enable_social_benefit', DEFAULTS.enable_social_benefit)
}

function isMarketingCampaignsEnabled() {
  return boolFlag('enable_marketing_campaigns', DEFAULTS.enable_marketing_campaigns)
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
    showFaceWelcome: !!isFaceWelcomeEnabled(),
    showSocialBenefit: !!isSocialBenefitEnabled(),
    showMarketingCampaigns: !!isMarketingCampaignsEnabled()
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
  invalidateClientConfig,
  currentConfigVersion,
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
  isSocialBenefitEnabled,
  isMarketingCampaignsEnabled,
  boolFlag,
  menuPageFlags,
  memberPageFlags,
  alipayPrivacyPageData,
  wxPrivacyPageData
}
