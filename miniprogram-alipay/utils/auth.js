const api = require('./api')
const app = getApp()

function readStorage(key) {
  try {
    const res = my.getStorageSync({ key })
    if (res && Object.prototype.hasOwnProperty.call(res, 'data')) return res.data
    return res
  } catch (_) {
    return null
  }
}

function isDevApiBase() {
  const base = String((app && app.globalData && app.globalData.apiBase) || '')
  return /127\.0\.0\.1|localhost|192\.168\.|10\.\d+\.|172\.(1[6-9]|2\d|3[01])\./.test(base)
}

function formatError(err, fallback) {
  if (!err) return fallback || '未知错误'
  if (typeof err === 'string') return err
  if (err.message) return err.message
  return err.errorMessage || err.errMsg || err.memo || fallback || '未知错误'
}

function getAuthCode() {
  return new Promise((resolve, reject) => {
    my.getAuthCode({
      scopes: 'auth_user',
      success: resolve,
      fail: (err) => {
        if (isDevApiBase()) {
          console.warn('[auth] getAuthCode 失败，开发环境使用 mock 登录', err)
          resolve({ authCode: 'mock:simulator' })
          return
        }
        reject(new Error(formatError(err, 'getAuthCode 失败，请确认 IDE 已登录支付宝沙箱账号')))
      }
    })
  })
}

async function ensureLogin(tableToken) {
  const cachedToken = app.globalData.customerToken || readStorage('customer_token')
  if (cachedToken) {
    app.globalData.customerToken = cachedToken
    app.globalData.customerId = app.globalData.customerId || readStorage('customer_id')
    app.globalData.customerNo = app.globalData.customerNo || readStorage('customer_no')
    app.globalData.nickname = app.globalData.nickname || readStorage('nickname') || ''
    return {
      customer_id: app.globalData.customerId,
      customer_no: app.globalData.customerNo,
      customer_token: cachedToken,
      nickname: app.globalData.nickname,
      need_member_prompt: !app.globalData.nickname
    }
  }

  const auth = await getAuthCode()
  if (!auth || !auth.authCode) {
    throw new Error('getAuthCode 未返回 authCode，请检查支付宝登录授权')
  }
  const data = await api.post('/miniprogram/alipay/login', {
    auth_code: auth.authCode,
    table_token: tableToken || app.globalData.tableToken || readStorage('table_token') || null,
    nickname: app.globalData.nickname || readStorage('nickname') || null
  }, false)
  if (!data || !data.customer_token) {
    throw new Error('登录成功但未返回 customer_token')
  }
  app.globalData.customerId = data.customer_id
  app.globalData.customerNo = data.customer_no
  app.globalData.customerToken = data.customer_token
  app.globalData.nickname = data.nickname || ''
  app.globalData.faceStatus = (data.face_status && data.face_status.consent_status) || 'NONE'
  if (data.table) {
    app.globalData.table = data.table
    app.globalData.tableToken = data.table.qr_token
    my.setStorageSync({ key: 'table_token', data: data.table.qr_token })
  }
  my.setStorageSync({ key: 'customer_id', data: data.customer_id })
  my.setStorageSync({ key: 'customer_token', data: data.customer_token })
  my.setStorageSync({ key: 'customer_no', data: data.customer_no || '' })
  if (data.nickname) my.setStorageSync({ key: 'nickname', data: data.nickname })
  return data
}

function saveCart(cart) {
  app.globalData.cart = cart
  my.setStorageSync({ key: 'cart', data: cart })
}

function getCart() {
  return app.globalData.cart || readStorage('cart') || []
}

module.exports = { ensureLogin, saveCart, getCart, formatError, readStorage }
