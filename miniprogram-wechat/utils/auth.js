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
  if (!app || !app.globalData) return
  app.globalData.customerToken = ''
  app.globalData.customerId = null
  app.globalData.customerNo = ''
  app.globalData.nickname = ''
  app.globalData.avatarUrl = ''
  app.globalData._customerIdSyncedForToken = null
  clearPrefsCache(app)
  try {
    wx.removeStorageSync('customer_token')
    wx.removeStorageSync('customer_id')
    wx.removeStorageSync('customer_no')
    // 库重置后本地昵称/头像属于已删除顾客，必须清掉，否则会写回新客并跳过注册
    wx.removeStorageSync('nickname')
    wx.removeStorageSync('avatar_url')
  } catch (_) {}
}

function isStaleCustomerAuthError(err) {
  const msg = String((err && err.message) || '')
  return /顾客不存在|已禁用|无效顾客|请先登录|令牌已失效|无效令牌|401/.test(msg)
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
  return String(err.errMsg || err.message || err.error || '')
}

/**
 * 静默探测顾客令牌是否有效（永不 reject，避免把 401 打成 Uncaught Error）
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
        const status = Number(res.statusCode)
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
  // 以服务端资料为准，避免沿用已删除顾客的本地昵称
  if (Object.prototype.hasOwnProperty.call(me, 'nickname') || me.display_name) {
    const nick = me.nickname || me.display_name || ''
    app.globalData.nickname = nick
    if (nick) wx.setStorageSync('nickname', nick)
    else {
      try { wx.removeStorageSync('nickname') } catch (_) {}
    }
  }
  if (Object.prototype.hasOwnProperty.call(me, 'avatar_url')) {
    app.globalData.avatarUrl = me.avatar_url || ''
    if (me.avatar_url) wx.setStorageSync('avatar_url', me.avatar_url)
    else {
      try { wx.removeStorageSync('avatar_url') } catch (_) {}
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
  // 失效或无法确认（含真机 401→fail / 网络失败）：一律视为无效，走重新登录
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
  app.globalData.nickname = data.nickname || ''
  if (data.avatar_url != null) app.globalData.avatarUrl = data.avatar_url || ''
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
    const valid = await validateCustomerToken(cachedToken)
    if (valid) {
      await syncCustomerIdFromMe(app, cachedToken)
      // 若 sync 发现失效已清会话，继续下方重新登录
      if (app.globalData.customerToken) {
        const nickname = app.globalData.nickname || ''
        return {
          customer_id: app.globalData.customerId,
          customer_no: app.globalData.customerNo,
          customer_token: app.globalData.customerToken,
          nickname,
          avatar_url: app.globalData.avatarUrl || '',
          need_member_prompt: isPlaceholderNickname(nickname),
          is_new: isPlaceholderNickname(nickname)
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
  // 不传本地缓存昵称/头像：新客资料只走 register，避免清库后本地昵称冒充已注册
  const data = await api.post('/miniprogram/wechat/login', {
    code: loginRes.code,
    table_token: tableToken || app.globalData.tableToken || readStorage('table_token') || null
  }, false)
  if (!data || !data.customer_token) {
    throw new Error('登录成功但未返回 customer_token')
  }
  applyLoginSession(app, data)
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
  if (data.nickname) wx.setStorageSync('nickname', data.nickname)
  else {
    try { wx.removeStorageSync('nickname') } catch (_) {}
  }
  if (data.avatar_url) wx.setStorageSync('avatar_url', data.avatar_url)
  else {
    try { wx.removeStorageSync('avatar_url') } catch (_) {}
  }
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
  clearCustomerSession,
  isPlaceholderNickname,
  isProfileComplete,
  isStaleCustomerAuthError,
  fetchCustomerMeSilent,
  purgeStaleSessionIfNeeded
}
