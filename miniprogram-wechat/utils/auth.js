const api = require('./api')

const PLACEHOLDER_NICKS = new Set(['联调员', '面馆会员', '微信用户', '支付宝用户'])

function isPlaceholderNickname(name) {
  const n = String(name || '').trim()
  return !n || PLACEHOLDER_NICKS.has(n)
}

/** 已登录且昵称非占位符，可跳过首页资料引导 */
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
    return wx.getStorageSync(key)
  } catch (_) {
    return null
  }
}

function formatError(err, fallback) {
  if (!err) return fallback || '未知错误'
  if (typeof err === 'string') return err
  if (err.message) return err.message
  return err.errorMessage || err.errMsg || err.memo || fallback || '未知错误'
}

function wxLogin() {
  return new Promise((resolve, reject) => {
    wx.login({
      success: resolve,
      fail: reject
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
  app.globalData.customerToken = ''
  app.globalData.customerId = null
  app.globalData.customerNo = ''
  app.globalData._customerIdSyncedForToken = null
  clearPrefsCache(app)
  try {
    wx.removeStorageSync('customer_token')
    wx.removeStorageSync('customer_id')
    wx.removeStorageSync('customer_no')
  } catch (_) {}
}

function isStaleCustomerAuthError(err) {
  const msg = String((err && err.message) || '')
  return /顾客不存在|已禁用|无效顾客|请先登录|令牌已失效|无效令牌|401/.test(msg)
}

function isStaleAuthMessage(msg) {
  return /顾客不存在|已禁用|无效顾客|请先登录|令牌已失效|无效令牌|401/.test(String(msg || ''))
}

/**
 * 静默探测顾客令牌是否有效（永不 reject，避免开发者工具把 401 打成 Uncaught Error）
 */
function probeCustomerMe(token) {
  const base = String((getAppSafe() && getAppSafe().globalData && getAppSafe().globalData.apiBase) || '')
  if (!base || !token) {
    return Promise.resolve({ ok: false, stale: true, data: null })
  }
  return new Promise((resolve) => {
    wx.request({
      url: `${base}/miniprogram/customers/me`,
      method: 'GET',
      timeout: 15000,
      header: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`
      },
      success(res) {
        const status = res.statusCode
        let body = res.data
        if (typeof body === 'string') {
          try { body = JSON.parse(body) } catch (_) { body = { message: body } }
        }
        body = body || {}
        const msg = body.message || (typeof body.detail === 'string' ? body.detail : '') || ''
        if (status >= 400 || (body.code !== 0 && body.code !== undefined)) {
          resolve({ ok: false, stale: isStaleAuthMessage(msg) || status === 401, data: null, message: msg })
          return
        }
        resolve({ ok: true, stale: false, data: body.data || null })
      },
      fail() {
        resolve({ ok: false, stale: false, data: null, network: true })
      }
    })
  })
}

function applyMeProfile(app, token, me) {
  if (!me) return
  if (me.customer_id) {
    app.globalData.customerId = me.customer_id
    wx.setStorageSync('customer_id', me.customer_id)
  }
  if (me.customer_no) {
    app.globalData.customerNo = me.customer_no
    wx.setStorageSync('customer_no', me.customer_no)
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
  if (probed.stale) return false
  return true
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
  clearPrefsCache(app)
}

function refreshCustomerToken(app, token, patch = {}) {
  if (token && token !== app.globalData.customerToken) {
    app.globalData._customerIdSyncedForToken = null
    clearPrefsCache(app)
  }
  if (token) {
    app.globalData.customerToken = token
    wx.setStorageSync('customer_token', token)
  }
  if (patch.customer_id != null) {
    app.globalData.customerId = patch.customer_id
    wx.setStorageSync('customer_id', patch.customer_id)
    if (token) app.globalData._customerIdSyncedForToken = token
  }
  if (patch.customer_no != null) {
    app.globalData.customerNo = patch.customer_no
    wx.setStorageSync('customer_no', patch.customer_no || '')
  }
}

async function ensureLogin(tableToken) {
  const app = requireApp()
  const cachedToken = app.globalData.customerToken || readStorage('customer_token')
  if (cachedToken) {
    app.globalData.customerToken = cachedToken
    app.globalData.customerId = app.globalData.customerId || readStorage('customer_id')
    app.globalData.customerNo = app.globalData.customerNo || readStorage('customer_no')
    app.globalData.nickname = app.globalData.nickname || readStorage('nickname') || ''
    app.globalData.avatarUrl = app.globalData.avatarUrl || readStorage('avatar_url') || ''
    const valid = await validateCustomerToken(cachedToken)
    if (valid) {
      await syncCustomerIdFromMe(app, cachedToken)
      if (app.globalData.customerToken) {
        return {
          customer_id: app.globalData.customerId,
          customer_no: app.globalData.customerNo,
          customer_token: app.globalData.customerToken,
          nickname: app.globalData.nickname,
          avatar_url: app.globalData.avatarUrl,
          need_member_prompt: isPlaceholderNickname(app.globalData.nickname)
        }
      }
    } else {
      clearCustomerSession(app)
    }
  }

  const loginRes = await wxLogin()
  if (!loginRes || !loginRes.code) {
    throw new Error('wx.login 未返回 code，请检查开发者工具登录状态')
  }
  const nickname = app.globalData.nickname || readStorage('nickname') || null
  const avatarUrl = app.globalData.avatarUrl || readStorage('avatar_url') || null
  const data = await api.post('/miniprogram/wechat/login', {
    code: loginRes.code,
    table_token: tableToken || app.globalData.tableToken || readStorage('table_token') || null,
    nickname,
    avatar_url: avatarUrl
  }, false)
  if (!data || !data.customer_token) {
    throw new Error('登录成功但未返回 customer_token')
  }
  applyLoginSession(app, data)
  app.globalData.nickname = data.nickname || nickname || ''
  app.globalData.avatarUrl = data.avatar_url || avatarUrl || ''
  if (data.table) {
    app.globalData.table = data.table
    app.globalData.tableToken = data.table.qr_token
    wx.setStorageSync('table_token', data.table.qr_token)
    if (data.table.store) {
      app.globalData.store = data.table.store
      wx.setStorageSync('dine_store', data.table.store)
    }
  }
  wx.setStorageSync('customer_id', data.customer_id)
  wx.setStorageSync('customer_token', data.customer_token)
  wx.setStorageSync('customer_no', data.customer_no || '')
  if (app.globalData.nickname) wx.setStorageSync('nickname', app.globalData.nickname)
  if (app.globalData.avatarUrl) wx.setStorageSync('avatar_url', app.globalData.avatarUrl)
  return data
}

/** 会员注册（完善资料）；遇旧 token 自动清会话重登后重试一次 */
async function registerMember(payload) {
  await ensureLogin()
  try {
    return await api.post('/miniprogram/member/register', payload || {})
  } catch (e) {
    if (!isStaleCustomerAuthError(e)) throw e
    clearCustomerSession(requireApp())
    await ensureLogin()
    return api.post('/miniprogram/member/register', payload || {})
  }
}

function saveCart(cart) {
  const app = requireApp()
  app.globalData.cart = cart
  wx.setStorageSync('cart', cart)
  require('./tabCartBadge').updateTabCartBadge(cart)
}

function getCart() {
  const app = getAppSafe()
  return (app && app.globalData && app.globalData.cart) || readStorage('cart') || []
}

module.exports = {
  ensureLogin,
  registerMember,
  saveCart,
  getCart,
  formatError,
  readStorage,
  getAppSafe,
  refreshCustomerToken,
  isPlaceholderNickname,
  isProfileComplete,
  fetchCustomerMeSilent,
  purgeStaleSessionIfNeeded
}
