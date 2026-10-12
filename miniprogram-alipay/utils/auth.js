const api = require('./api')
const { getEnvDebugInfo } = require('../config/env')

const PLACEHOLDER_NICKS = new Set(['联调员', '面馆会员', '微信用户', '支付宝用户'])
const DRAFT_NICK_KEY = 'reg_draft_nickname'
const DRAFT_AVATAR_KEY = 'reg_draft_avatar'

function isPlaceholderNickname(name) {
  const n = String(name || '').trim()
  return !n || PLACEHOLDER_NICKS.has(n)
}

function isProfileComplete(source) {
  const src = source || {}
  const token = src.customer_token || src.customerToken || readStorage('customer_token')
  if (!token) return false
  const nickname = src.nickname
    || (src.globalData && src.globalData.nickname)
    || readStorage('nickname')
    || ''
  return !isPlaceholderNickname(nickname)
}

function getToken() {
  const app = getAppSafe()
  return String(
    (app && app.globalData && app.globalData.customerToken) || readStorage('customer_token') || ''
  ).trim()
}

function getAppSafe() {
  try {
    return getApp()
  } catch (_) {
    return null
  }
}

function requireApp() {
  const app = getAppSafe()
  if (!app || !app.globalData) {
    throw new Error('应用未就绪，请重新打开小程序')
  }
  return app
}

function readStorage(key) {
  try {
    const res = my.getStorageSync({ key })
    if (res && Object.prototype.hasOwnProperty.call(res, 'data')) return res.data
    return res
  } catch (_) {
    return null
  }
}

function writeStorage(key, data) {
  try {
    my.setStorageSync({ key, data })
  } catch (_) { }
}

function removeStorage(key) {
  try { my.removeStorageSync({ key }) } catch (_) { }
}

function readRegDraft() {
  return {
    nickname: String(readStorage(DRAFT_NICK_KEY) || '').trim(),
    avatarUrl: String(readStorage(DRAFT_AVATAR_KEY) || '').trim()
  }
}

function writeRegDraft(patch) {
  if (!patch) return
  if (Object.prototype.hasOwnProperty.call(patch, 'nickname')) {
    const n = String(patch.nickname || '').trim()
    if (n) writeStorage(DRAFT_NICK_KEY, n)
    else removeStorage(DRAFT_NICK_KEY)
  }
  if (Object.prototype.hasOwnProperty.call(patch, 'avatarUrl')) {
    const a = String(patch.avatarUrl || '').trim()
    if (a) writeStorage(DRAFT_AVATAR_KEY, a)
    else removeStorage(DRAFT_AVATAR_KEY)
  }
}

function clearRegDraft() {
  removeStorage(DRAFT_NICK_KEY)
  removeStorage(DRAFT_AVATAR_KEY)
}

function resolveNeedMemberPrompt(data) {
  if (data && Object.prototype.hasOwnProperty.call(data, 'need_member_prompt')) {
    return !!data.need_member_prompt
  }
  return isPlaceholderNickname(data && data.nickname)
}

function formatError(err, fallback) {
  if (!err) return fallback || '未知错误'
  if (typeof err === 'string') return err
  if (err.message) return err.message
  return err.errorMessage || err.errMsg || err.memo || fallback || '未知错误'
}

function getAuthCode() {
  return new Promise((resolve, reject) => {
    const env = getEnvDebugInfo()
    // 开发者工具统一用稳定 mock，避免 IDE 真实 authCode / mock 混用导致重复建客
    if (env && env.isDevtools) {
      console.warn('[auth] 开发者工具使用稳定 mock 登录（mock:simulator）')
      resolve({ authCode: 'mock:simulator' })
      return
    }
    my.getAuthCode({
      scopes: 'auth_user',
      success: resolve,
      fail: (err) => {
        const msg = formatError(err, '')
        // 真机偶发「未关联」类错误时仍允许 mock，避免卡死联调
        const ideAuthGap = /关联小程序|选择关联|请先\[选择/i.test(msg)
        if (ideAuthGap) {
          console.warn('[auth] getAuthCode 失败，使用 mock 登录', err)
          resolve({ authCode: 'mock:simulator' })
          return
        }
        reject(new Error(formatError(err, 'getAuthCode 失败，请确认已登录支付宝并授权')))
      }
    })
  })
}

function clearPrefsCache(app) {
  app.globalData.prefs = []
  app.globalData.prefsView = null
  app.globalData.prefsAt = 0
}

function shouldSyncCustomerId(app, token) {
  return app.globalData._customerIdSyncedForToken !== token
}

function clearCustomerSession(app) {
  if (!app || !app.globalData) return
  app.globalData.customerToken = ''
  app.globalData.customerId = null
  app.globalData.customerNo = ''
  app.globalData.nickname = ''
  app.globalData.avatarUrl = ''
  app.globalData.needMemberPrompt = null
  app.globalData._customerIdSyncedForToken = null
  clearPrefsCache(app)
  try {
    my.removeStorageSync({ key: 'customer_token' })
    my.removeStorageSync({ key: 'customer_id' })
    my.removeStorageSync({ key: 'customer_no' })
    // P2-5：清除 token 保存时间
    my.removeStorageSync({ key: 'customer_token_saved_at' })
    // 库重置后本地昵称/头像属于已删除顾客，必须清掉，否则会写回新客并跳过注册
    my.removeStorageSync({ key: 'nickname' })
    my.removeStorageSync({ key: 'avatar_url' })
  } catch (_) { }
}

function isStaleCustomerAuthError(err) {
  const msg = String((err && err.message) || '')
  return /顾客不存在|已禁用|无效顾客|请先登录|令牌已失效|无效令牌|401/.test(msg)
}

/**
 * P2-3：顾客登出。通知服务端递增 token_version 吊销旧 token，并清除本地会话。
 * 即使服务端调用失败（无网络/旧 token 已失效），也强制清本地缓存。
 */
async function logoutCustomer() {
  const app = getAppSafe()
  try {
    await api.post('/miniprogram/logout', {}, true)
  } catch (_) { }
  if (app) clearCustomerSession(app)
}

function isStaleAuthMessage(msg) {
  return /顾客不存在|已禁用|无效顾客|请先登录|令牌已失效|无效令牌|401/.test(String(msg || ''))
}

function extractRequestFailMessage(err) {
  if (!err) return ''
  if (typeof err === 'string') return err
  const data = err.data
  if (data && typeof data === 'object') {
    if (data.message) return String(data.message)
    if (typeof data.detail === 'string') return data.detail
  }
  // 支付宝 fail 常用 errorMessage，微信常用 errMsg；两端都检查以保持同步
  return String(err.errorMessage || err.errMsg || err.message || err.error || '')
}

/**
 * 探测单飞：冷启动时 app.onLaunch 清会话、首页 ensureLogin（validate + sync）、菜单页偏好
 * 几乎同时触发；同一 token 在 10 秒内只发一次 /customers/me 并复用结果（含失败），
 * 避免启动请求风暴拖慢真机调试。
 */
let probeState = null
const PROBE_DEDUP_MS = 10000

/**
 * 静默探测顾客令牌是否有效（永不 reject，避免把 401 打成 Uncaught Error）
 */
function probeCustomerMe(token) {
  const base = String((getAppSafe() && getAppSafe().globalData && getAppSafe().globalData.apiBase) || '')
  if (!base || !token) {
    return Promise.resolve({ ok: false, stale: true, data: null })
  }
  const now = Date.now()
  if (probeState && probeState.token === token && now - probeState.at < PROBE_DEDUP_MS) {
    // P2-4：网络失败的探测结果不缓存，允许网络恢复后立即重试
    if (!probeState.isNetworkError) {
      return probeState.promise
    }
  }
  const promise = new Promise((resolve) => {
    my.request({
      url: `${base}/miniprogram/customers/me`,
      method: 'GET',
      timeout: 15000,
      headers: {
        'Content-Type': 'application/json; charset=utf-8',
        Authorization: `Bearer ${token}`
      },
      success(res) {
        const status = Number(res.status != null ? res.status : res.statusCode)
        let body = res.data
        if (typeof body === 'string') {
          try { body = JSON.parse(body) } catch (_) { body = { message: body } }
        }
        body = body || {}
        const msg = body.message || (typeof body.detail === 'string' ? body.detail : '') || ''
        if ((status && status >= 400) || (body.code !== 0 && body.code !== undefined)) {
          resolve({
            ok: false,
            stale: isStaleAuthMessage(msg) || status === 401,
            data: null,
            message: msg
          })
          return
        }
        resolve({ ok: true, stale: false, data: body.data || null })
      },
      fail(err) {
        const msg = extractRequestFailMessage(err)
        // 部分真机把 HTTP 401 走 fail，须按失效处理；纯网络失败标记 network，由 validate 统一判无效
        if (isStaleAuthMessage(msg)) {
          resolve({ ok: false, stale: true, data: null, message: msg })
          return
        }
        resolve({ ok: false, stale: false, data: null, network: true, message: msg })
      }
    })
  })
  probeState = { token, at: now, promise, isNetworkError: false }
  // P2-4：网络失败结果不缓存，允许恢复后立即重试
  promise.then((result) => {
    if (probeState && probeState.token === token) {
      probeState.isNetworkError = !!result.network
    }
  })
  return promise
}

function applyMeProfile(app, token, me) {
  if (!me) return
  if (me.customer_id) {
    app.globalData.customerId = me.customer_id
    my.setStorageSync({ key: 'customer_id', data: me.customer_id })
  }
  if (me.customer_no) {
    app.globalData.customerNo = me.customer_no
    my.setStorageSync({ key: 'customer_no', data: me.customer_no })
  }
  const needPrompt = resolveNeedMemberPrompt(me)
  app.globalData.needMemberPrompt = needPrompt
  if (needPrompt) {
    app.globalData.nickname = ''
    app.globalData.avatarUrl = ''
    if (token) app.globalData._customerIdSyncedForToken = token
    return
  }
  // 以服务端资料为准，避免沿用已删除顾客的本地昵称
  if (Object.prototype.hasOwnProperty.call(me, 'nickname') || me.display_name) {
    const nick = me.nickname || me.display_name || ''
    app.globalData.nickname = nick
    if (nick) my.setStorageSync({ key: 'nickname', data: nick })
    else {
      try { my.removeStorageSync({ key: 'nickname' }) } catch (_) { }
    }
  }
  if (Object.prototype.hasOwnProperty.call(me, 'avatar_url')) {
    app.globalData.avatarUrl = me.avatar_url || ''
    if (me.avatar_url) my.setStorageSync({ key: 'avatar_url', data: me.avatar_url })
    else {
      try { my.removeStorageSync({ key: 'avatar_url' }) } catch (_) { }
    }
  }
  if (token) app.globalData._customerIdSyncedForToken = token
}

async function validateCustomerToken(token) {
  if (!token) return false
  const app = requireApp()
  app.globalData.customerToken = token
  const probed = await probeCustomerMe(token)
  if (probed.ok) {
    applyMeProfile(app, token, probed.data)
    return true
  }
  // P1-2：网络瞬断不应清会话强制重新登录；仅在 401/stale 时才视为无效
  if (probed.network) return true
  return false
}

async function syncCustomerIdFromMe(app, token) {
  if (!token || !shouldSyncCustomerId(app, token)) return
  app.globalData.customerToken = token
  const probed = await probeCustomerMe(token)
  if (probed.stale) {
    clearCustomerSession(app)
    return
  }
  if (!probed.ok || !probed.data) return
  applyMeProfile(app, token, probed.data)
}

/** 静默拉取 /customers/me；失效则清会话，永不抛错 */
async function fetchCustomerMeSilent() {
  const app = getAppSafe()
  const token = (app && app.globalData && app.globalData.customerToken) || readStorage('customer_token')
  if (!token) return null
  const probed = await probeCustomerMe(token)
  if (probed.stale) {
    if (app && app.globalData) clearCustomerSession(app)
    return null
  }
  if (probed.ok && probed.data && app && app.globalData) {
    applyMeProfile(app, token, probed.data)
  }
  return probed.ok ? probed.data : null
}

/** 启动时清掉库重置后残留的本地 token（不阻塞、不抛错） */
function purgeStaleSessionIfNeeded() {
  const app = getAppSafe()
  const token = (app && app.globalData && app.globalData.customerToken) || readStorage('customer_token')
  if (!token || !app) return Promise.resolve(false)
  // P2-5：本地 token 超过 7 天直接清除，不再探测
  const savedAt = readStorage('customer_token_saved_at') || 0
  if (savedAt && Date.now() - Number(savedAt) > 7 * 24 * 60 * 60 * 1000) {
    clearCustomerSession(app)
    return Promise.resolve(true)
  }
  return probeCustomerMe(token).then((probed) => {
    if (probed.stale) {
      clearCustomerSession(app)
      return true
    }
    if (probed.ok) applyMeProfile(app, token, probed.data)
    return false
  })
}

function applyLoginSession(app, data) {
  app.globalData.customerId = data.customer_id
  app.globalData.customerNo = data.customer_no
  app.globalData.customerToken = data.customer_token
  app.globalData._customerIdSyncedForToken = data.customer_token
  const needPrompt = resolveNeedMemberPrompt(data)
  app.globalData.needMemberPrompt = needPrompt
  if (needPrompt) {
    app.globalData.nickname = ''
    app.globalData.avatarUrl = ''
  } else {
    app.globalData.nickname = data.nickname || ''
    if (data.avatar_url != null) app.globalData.avatarUrl = data.avatar_url || ''
  }
  clearPrefsCache(app)
}

function refreshCustomerToken(app, token, patch = {}) {
  if (token && token !== app.globalData.customerToken) {
    app.globalData._customerIdSyncedForToken = null
    clearPrefsCache(app)
  }
  if (token) {
    app.globalData.customerToken = token
    my.setStorageSync({ key: 'customer_token', data: token })
    // P2-5：记录 token 保存时间
    my.setStorageSync({ key: 'customer_token_saved_at', data: Date.now() })
  }
  if (patch.customer_id != null) {
    app.globalData.customerId = patch.customer_id
    my.setStorageSync({ key: 'customer_id', data: patch.customer_id })
    if (token) app.globalData._customerIdSyncedForToken = token
  }
  if (patch.customer_no != null) {
    app.globalData.customerNo = patch.customer_no
    my.setStorageSync({ key: 'customer_no', data: patch.customer_no || '' })
  }
}

async function doEnsureLogin(tableToken, opts) {
  const app = requireApp()
  const forceRelogin = !!(opts && opts.forceRelogin)
  // storage 优先，与微信 / api.resolveCustomerToken 对齐
  const cachedToken = readStorage('customer_token') || app.globalData.customerToken
  if (!forceRelogin && cachedToken) {
    app.globalData.customerToken = cachedToken
    app.globalData.customerId = app.globalData.customerId || readStorage('customer_id')
    app.globalData.customerNo = app.globalData.customerNo || readStorage('customer_no')
    const valid = await validateCustomerToken(cachedToken)
    if (valid) {
      await syncCustomerIdFromMe(app, cachedToken)
      // 若 sync 发现失效已清会话，继续下方重新登录
      if (app.globalData.customerToken) {
        const needPrompt = app.globalData.needMemberPrompt
        const nickname = needPrompt ? '' : (app.globalData.nickname || '')
        return {
          customer_id: app.globalData.customerId,
          customer_no: app.globalData.customerNo,
          customer_token: app.globalData.customerToken,
          nickname,
          avatar_url: needPrompt ? '' : (app.globalData.avatarUrl || ''),
          need_member_prompt: needPrompt == null ? isPlaceholderNickname(app.globalData.nickname || '') : !!needPrompt,
          is_new: needPrompt == null ? isPlaceholderNickname(app.globalData.nickname || '') : !!needPrompt
        }
      }
    } else {
      clearCustomerSession(app)
    }
  }

  const authRes = await getAuthCode()
  if (!authRes || !authRes.authCode) {
    throw new Error('getAuthCode 未返回 authCode，请检查支付宝登录授权')
  }
  // 不传本地缓存昵称/头像：新客资料只走 register，避免清库后本地昵称冒充已注册
  const data = await api.post('/miniprogram/alipay/login', {
    auth_code: authRes.authCode,
    table_token: tableToken || app.globalData.tableToken || readStorage('table_token') || null
  }, false)
  if (!data || !data.customer_token) {
    throw new Error('登录成功但未返回 customer_token')
  }
  applyLoginSession(app, data)
  if (data.table) {
    // 统一走 applyContext 写全 dine_table/dine_store/table_token/dine_bound_at，
    // 旧逻辑漏写 dine_table 与 dine_bound_at，会导致其它页面误判绑定过期而
    // 反复要求「扫码绑桌」。
    try {
      require('./context').applyContext(data.table, data.table.store || null)
    } catch (_) {
      app.globalData.table = data.table
      if (data.table.qr_token) {
        app.globalData.tableToken = data.table.qr_token
        my.setStorageSync({ key: 'table_token', data: data.table.qr_token })
      }
      if (data.table.store) {
        app.globalData.store = data.table.store
        my.setStorageSync({ key: 'dine_store', data: data.table.store })
      }
    }
  }
  my.setStorageSync({ key: 'customer_id', data: data.customer_id })
  my.setStorageSync({ key: 'customer_token', data: data.customer_token })
  // P2-5：记录 token 保存时间
  my.setStorageSync({ key: 'customer_token_saved_at', data: Date.now() })
  my.setStorageSync({ key: 'customer_no', data: data.customer_no || '' })
  if (resolveNeedMemberPrompt(data)) {
    try { my.removeStorageSync({ key: 'nickname' }) } catch (_) { }
    try { my.removeStorageSync({ key: 'avatar_url' }) } catch (_) { }
  } else {
    if (data.nickname) my.setStorageSync({ key: 'nickname', data: data.nickname })
    else {
      try { my.removeStorageSync({ key: 'nickname' }) } catch (_) { }
    }
    if (data.avatar_url) my.setStorageSync({ key: 'avatar_url', data: data.avatar_url })
    else {
      try { my.removeStorageSync({ key: 'avatar_url' }) } catch (_) { }
    }
  }
  return data
}

/**
 * 登录单飞：首页 12 秒兜底跳菜单后，菜单/会员页再调 ensureLogin 时复用同一在途登录，
 * 弱网下避免重复弹授权、重复请求 /alipay/login。forceRelogin 不复用。
 */
let loginInflight = null
function ensureLogin(tableToken, opts) {
  const forceRelogin = !!(opts && opts.forceRelogin)
  if (!forceRelogin && loginInflight) return loginInflight
  const promise = doEnsureLogin(tableToken, opts).then(
    (v) => {
      if (loginInflight === promise) loginInflight = null
      return v
    },
    (e) => {
      if (loginInflight === promise) loginInflight = null
      throw e
    }
  )
  if (!forceRelogin) loginInflight = promise
  return promise
}

/** 主动开通智能会员（幂等）；兼容旧 register 路径 */
async function activateMember(payload) {
  await ensureLogin()
  const app = requireApp()
  const channel = (app.globalData && app.globalData.channel) || ''
  const body = Object.assign({}, payload || {})
  if (channel === 'wechat' || channel === 'alipay') body.channel = channel
  if (!body.table_token) {
    body.table_token = (app.globalData && app.globalData.tableToken) || readStorage('table_token') || null
  }
  try {
    return await api.post('/miniprogram/member/activate', body)
  } catch (e) {
    if (e && (e.code === 40400 || e.status === 404 || /not found|404/i.test(String(e.message || '')))) {
      return registerMember(body)
    }
    if (!isStaleCustomerAuthError(e)) throw e
    clearCustomerSession(requireApp())
    await ensureLogin()
    return api.post('/miniprogram/member/activate', body)
  }
}

/** 仅更新资料，不激活会员、不发券。优先 PUT（支付宝等端对 PATCH 支持不稳定）。 */
async function patchMemberProfile(payload) {
  await ensureLogin()
  const app = requireApp()
  const channel = (app.globalData && app.globalData.channel) || ''
  const body = Object.assign({}, payload || {})
  if (channel === 'wechat' || channel === 'alipay') body.channel = channel
  const send = () => api.put('/miniprogram/member/profile', body)
  try {
    return await send()
  } catch (e) {
    if (!isStaleCustomerAuthError(e)) throw e
    clearCustomerSession(requireApp())
    await ensureLogin()
    return send()
  }
}

/** 会员注册（完善资料）；遇旧 token 自动清会话重登后重试一次 */
async function registerMember(payload) {
  await ensureLogin()
  const app = requireApp()
  const channel = (app.globalData && app.globalData.channel) || ''
  const body = Object.assign({}, payload || {})
  if (channel === 'wechat' || channel === 'alipay') body.channel = channel
  if (!body.table_token) {
    body.table_token = (app.globalData && app.globalData.tableToken) || readStorage('table_token') || null
  }
  try {
    return await api.post('/miniprogram/member/register', body)
  } catch (e) {
    if (!isStaleCustomerAuthError(e)) throw e
    clearCustomerSession(requireApp())
    await ensureLogin()
    return api.post('/miniprogram/member/register', body)
  }
}

// P2-1 购物车按门店隔离：cart 始终绑定 cart_store_id，跨店必须清空，防止串单
const CART_STORE_ID_KEY = 'cart_store_id'

function currentStoreId() {
  const app = getAppSafe()
  const g = app && app.globalData
  const store = g && g.store
  const table = g && g.table
  return (store && (store.id || store.store_id)) || (table && table.store_id) || null
}

function saveCart(cart, options) {
  const app = requireApp()
  const opts = options || {}
  app.globalData.cart = cart
  my.setStorageSync({ key: 'cart', data: cart })
  // 显式 store_id > 已绑定的 cart_store_id > 当前桌台门店
  // 避免点餐页 refreshCartBar 无参 saveCart 把「再来一单」的门店冲成当前桌台店
  let sid = opts.store_id
  if (sid == null) {
    const bound = Number(readStorage(CART_STORE_ID_KEY))
    if (Number.isFinite(bound) && bound > 0) sid = bound
    else sid = currentStoreId()
  }
  if (sid != null) {
    my.setStorageSync({ key: CART_STORE_ID_KEY, data: Number(sid) || 0 })
  } else {
    try { my.removeStorageSync({ key: CART_STORE_ID_KEY }) } catch (_) { }
  }
}

function getCart() {
  const app = getAppSafe()
  return (app && app.globalData && app.globalData.cart) || readStorage('cart') || []
}

/**
 * P2-1：门店切换时检测并清空跨店购物车。
 * 在 entry/扫码绑定/启动恢复后调用。返回 true 表示已清空。
 */
function ensureCartForCurrentStore() {
  const app = getAppSafe()
  if (!app || !app.globalData) return false
  const sid = currentStoreId()
  if (sid == null) return false
  const boundSidRaw = readStorage(CART_STORE_ID_KEY)
  const boundSid = Number(boundSidRaw)
  if (!Number.isFinite(boundSid) || boundSid <= 0) {
    // 旧版本未写入 cart_store_id 且购物车非空，且当前门店已确定 → 一律视为跨店，清空
    const cart = app.globalData.cart || readStorage('cart') || []
    if (cart.length) {
      saveCart([], { store_id: sid })
      return true
    }
    // 购物车本就空：补记当前门店
    try { my.setStorageSync({ key: CART_STORE_ID_KEY, data: Number(sid) || 0 }) } catch (_) { }
    return false
  }
  if (boundSid !== Number(sid)) {
    saveCart([], { store_id: sid })
    return true
  }
  return false
}

module.exports = {
  ensureLogin,
  registerMember,
  activateMember,
  patchMemberProfile,
  saveCart,
  getCart,
  getToken,
  currentStoreId,
  ensureCartForCurrentStore,
  logoutCustomer,
  formatError,
  readStorage,
  getAppSafe,
  refreshCustomerToken,
  clearCustomerSession,
  isPlaceholderNickname,
  isProfileComplete,
  isStaleCustomerAuthError,
  fetchCustomerMeSilent,
  purgeStaleSessionIfNeeded,
  readRegDraft,
  writeRegDraft,
  clearRegDraft
}
