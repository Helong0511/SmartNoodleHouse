const api = require('./api')

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

async function validateCustomerToken(token) {
  if (!token) return false
  try {
    await api.get('/miniprogram/customers/me')
    return true
  } catch (e) {
    if (isStaleCustomerAuthError(e)) return false
    // 网络抖动等：保留本地 token，避免反复登出
    return true
  }
}

async function syncCustomerIdFromMe(app, token) {
  if (!token || !shouldSyncCustomerId(app, token)) return
  try {
    const me = await api.get('/miniprogram/customers/me')
    if (me && me.customer_id) {
      app.globalData.customerId = me.customer_id
      wx.setStorageSync('customer_id', me.customer_id)
    }
    if (me && me.customer_no) {
      app.globalData.customerNo = me.customer_no
      wx.setStorageSync('customer_no', me.customer_no)
    }
    app.globalData._customerIdSyncedForToken = token
  } catch (e) {
    if (isStaleCustomerAuthError(e)) {
      clearCustomerSession(app)
      throw e
    }
    /* 其它失败不标记，下次 ensureLogin 重试 */
  }
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
      return {
        customer_id: app.globalData.customerId,
        customer_no: app.globalData.customerNo,
        customer_token: cachedToken,
        nickname: app.globalData.nickname,
        avatar_url: app.globalData.avatarUrl,
        need_member_prompt: !app.globalData.nickname
      }
    }
    clearCustomerSession(app)
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
  app.globalData.faceStatus = (data.face_status && data.face_status.consent_status) || 'NONE'
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
  saveCart,
  getCart,
  readStorage,
  getAppSafe,
  refreshCustomerToken
}
