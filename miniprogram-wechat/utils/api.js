const { resolveApiBase } = require('../config/env')

const DEFAULT_API_BASE = resolveApiBase()

function getAppSafe() {
  try {
    return getApp()
  } catch (_) {
    return null
  }
}

/** 优先 globalData，否则用 config/env 默认值（Tab 页冷启动时 getApp 可能暂不可用） */
function resolveBaseUrl() {
  const app = getAppSafe()
  if (app && app.globalData) {
    const configured = String(app.globalData.apiBase || '').trim()
    if (configured) return configured
    app.globalData.apiBase = DEFAULT_API_BASE
    return DEFAULT_API_BASE
  }
  return DEFAULT_API_BASE
}

function authHeader() {
  const app = getAppSafe()
  const token = wx.getStorageSync('customer_token') || (app && app.globalData && app.globalData.customerToken)
  return token ? { Authorization: `Bearer ${token}` } : {}
}

function parseResponseBody(raw) {
  if (raw == null) return {}
  if (typeof raw === 'string') {
    const text = raw.trim()
    if (!text) return {}
    try {
      return JSON.parse(text)
    } catch (_) {
      return { message: text }
    }
  }
  if (typeof raw === 'object') return raw
  return {}
}

function pickErrorMessage(resOrErr, fallback) {
  if (!resOrErr) return fallback
  if (typeof resOrErr === 'string') return resOrErr
  const body = parseResponseBody(resOrErr.data)
  if (typeof resOrErr.data === 'string' && resOrErr.data.trim()) return resOrErr.data.trim()
  if (body.message) return body.message
  if (body.detail) {
    return typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
  }
  const status = resOrErr.statusCode
  if (status != null && status >= 400) {
    return fallback || `请求失败(${status})`
  }
  const errMsg = resOrErr.errMsg
  if (errMsg && errMsg !== 'request:ok') return errMsg
  if (resOrErr.message) return resOrErr.message
  return fallback
}

function networkHint(base, msg) {
  const m = String(msg || '')
  if (/CONNECTION_RESET|ERR_CONNECTION_RESET|reset by peer/i.test(m)) {
    return '连接被重置：请确认后端已启动且未崩溃重启。开发：cd backend && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000'
  }
  if (/CONNECTION_REFUSED|connect fail|无法连接|ECONNREFUSED/i.test(m)) {
    return '无法连接后端，请先启动：cd backend && uvicorn app.main:app --host 0.0.0.0 --port 8000'
  }
  if (/127\.0\.0\.1|localhost/.test(base || '') && /fail|timeout|CONNECTION|reset/i.test(m)) {
    return `${m}；真机/部分模拟器请把 config/env.js 的 DEV_LAN_HOST 改为电脑局域网 IP，并勾选「不校验合法域名」`
  }
  return m || '网络请求失败'
}

function isTransientNetworkError(msg) {
  return /CONNECTION_RESET|ERR_CONNECTION_RESET|reset by peer|CONNECTION_REFUSED|ECONNREFUSED|timeout/i.test(String(msg || ''))
}

function requestOnce(method, path, data = {}, needAuth = true) {
  const base = resolveBaseUrl()
  return new Promise((resolve, reject) => {
    if (!base) {
      reject(new Error('未配置 apiBase，请在 miniprogram-wechat/config/env.js 填写 PROD_API_BASE 或 DEV_LAN_HOST'))
      return
    }
    wx.request({
      url: `${base}${path}`,
      method,
      data,
      timeout: 10000,
      header: {
        'Content-Type': 'application/json',
        ...(needAuth ? authHeader() : {})
      },
      success(res) {
        const status = res.statusCode
        const body = parseResponseBody(res.data)
        if (status != null && status >= 400) {
          reject(new Error(pickErrorMessage(res, `请求失败(${status})`)))
          return
        }
        if (body.code !== 0 && body.code !== undefined) {
          reject(new Error(body.message || '请求失败'))
          return
        }
        resolve(body.data)
      },
      fail(err) {
        reject(new Error(networkHint(base, pickErrorMessage(err, '网络请求失败'))))
      }
    })
  })
}

function request(method, path, data = {}, needAuth = true, retries = 1) {
  return requestOnce(method, path, data, needAuth).catch((err) => {
    if (retries > 0 && isTransientNetworkError(err && err.message)) {
      return new Promise((resolve, reject) => {
        setTimeout(() => {
          request(method, path, data, needAuth, retries - 1).then(resolve).catch(reject)
        }, 400)
      })
    }
    throw err
  })
}

module.exports = {
  get: (path, data, needAuth = true) => request('GET', path, data, needAuth),
  post: (path, data, needAuth = true) => request('POST', path, data, needAuth),
  put: (path, data, needAuth = true) => request('PUT', path, data, needAuth),
  patch: (path, data, needAuth = true) => request('PATCH', path, data, needAuth)
}
