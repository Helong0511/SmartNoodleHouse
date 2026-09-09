const { resolveApiBase } = require('../config/env')

const DEFAULT_API_BASE = resolveApiBase()

function getAppSafe() {
  try {
    return getApp()
  } catch (_) {
    return null
  }
}

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

function readStorage(key) {
  try {
    const res = my.getStorageSync({ key })
    if (res && Object.prototype.hasOwnProperty.call(res, 'data')) return res.data
    return res
  } catch (_) {
    return null
  }
}

function resolveCustomerToken() {
  const app = getAppSafe()
  const token = String(
    (app && app.globalData && app.globalData.customerToken)
    || readStorage('customer_token')
    || ''
  ).trim()
  if (app && app.globalData && token) {
    app.globalData.customerToken = token
  }
  return token || null
}

function authHeader() {
  const token = resolveCustomerToken()
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
  if (body.message) return body.message
  if (body.detail) {
    return typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
  }
  const status = resOrErr.status != null ? resOrErr.status : resOrErr.statusCode
  if (status >= 400) {
    return fallback || `请求失败(${status})`
  }
  const errMsg = resOrErr.errorMessage || resOrErr.errMsg || resOrErr.message
  if (errMsg && errMsg !== 'request:ok') return errMsg
  return fallback
}

function networkHint(base, msg) {
  const m = String(msg || '')
  // 支付宝 IDE：未勾选「忽略 httpRequest 域名合法性检查」时常见 error 81
  if (/域名白名单|无权访问域名|error:\s*81|错误码\s*81|域名合法性/i.test(m)) {
    return (
      '未放行请求域名：支付宝开发者工具 → 右上角「详情」→「域名信息」→ 勾选' +
      '「忽略 httpRequest 域名合法性检查」（仅模拟器/预览/真机调试生效；正式版须配置 HTTPS 白名单）'
    )
  }
  if (/CONNECTION_RESET|ERR_CONNECTION_RESET|reset by peer/i.test(m)) {
    return '连接被重置：请确认后端已启动且未崩溃重启。开发：cd backend && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000'
  }
  if (/CONNECTION_REFUSED|connect fail|无法连接|ECONNREFUSED/i.test(m)) {
    return '无法连接后端，请先启动：cd backend && uvicorn app.main:app --host 0.0.0.0 --port 8000'
  }
  if (/127\.0\.0\.1|localhost/.test(base || '') && /fail|timeout|CONNECTION|reset/i.test(m)) {
    return `${m}；真机请把 config/env.js 的 DEV_LAN_HOST 改为电脑局域网 IP（如 192.168.x.x），后端须 --host 0.0.0.0`
  }
  return m || '网络请求失败'
}

function isTransientNetworkError(msg) {
  return /CONNECTION_RESET|ERR_CONNECTION_RESET|reset by peer|CONNECTION_REFUSED|ECONNREFUSED|timeout/i.test(String(msg || ''))
}

function isStaleAuthMessage(msg) {
  return /顾客不存在|已禁用|无效顾客|请先登录|令牌已失效|无效令牌/.test(String(msg || ''))
}

/** 用普通对象 reject，避免支付宝真机把 Error 打成 Uncaught Error */
function makeRequestError(message, extra) {
  return Object.assign({ message: String(message || '请求失败') }, extra || {})
}

function clearStaleCustomerAuthQuietly() {
  try {
    const auth = require('./auth')
    const app = getAppSafe()
    if (auth && typeof auth.clearCustomerSession === 'function') {
      auth.clearCustomerSession(app || { globalData: {} })
    }
  } catch (_) {}
}

function buildQueryUrl(path, data) {
  if (!data || typeof data !== 'object') return path
  const parts = []
  Object.keys(data).forEach((key) => {
    const val = data[key]
    if (val == null || val === '') return
    parts.push(`${encodeURIComponent(key)}=${encodeURIComponent(String(val))}`)
  })
  if (!parts.length) return path
  return `${path}${path.indexOf('?') >= 0 ? '&' : '?'}${parts.join('&')}`
}

function requestOnce(method, path, data = {}, needAuth = true) {
  const base = resolveBaseUrl()
  return new Promise((resolve, reject) => {
    if (!base) {
      reject(makeRequestError('未配置 apiBase，请在 miniprogram-alipay/config/env.js 填写 PROD_API_BASE 或 DEV_LAN_HOST'))
      return
    }
    const upper = String(method || 'GET').toUpperCase()
    // 支付宝部分基础库 GET + data + application/json 不会把参数拼到 URL，导致 entry 收不到 token
    const urlPath = upper === 'GET' ? buildQueryUrl(path, data) : path
    const req = {
      url: `${base}${urlPath}`,
      method: upper,
      dataType: 'json',
      timeout: 20000,
      headers: {
        'Content-Type': 'application/json',
        ...(needAuth ? authHeader() : {})
      },
      success(res) {
        const status = Number(res.status != null ? res.status : res.statusCode)
        const body = parseResponseBody(res.data)
        const msg = pickErrorMessage(res, `请求失败(${status || '?'})`)
        const codeNum = body.code == null ? undefined : Number(body.code)
        if ((status && status >= 400) || (codeNum !== undefined && codeNum !== 0 && !Number.isNaN(codeNum))) {
          try {
            require('./logger').reportApi('ERROR', upper, path, { status, code: body.code, message: msg })
          } catch (_) {}
          if (needAuth && (status === 401 || isStaleAuthMessage(msg))) {
            clearStaleCustomerAuthQuietly()
            reject(makeRequestError(msg, { code: 'AUTH_STALE', status: status || 401 }))
            return
          }
          reject(makeRequestError(msg, { status, code: body.code }))
          return
        }
        resolve(body.data)
      },
      fail(err) {
        const msg = networkHint(base, pickErrorMessage(err, '网络请求失败'))
        try {
          require('./logger').reportApi('ERROR', upper, path, { message: msg })
        } catch (_) {}
        if (needAuth && isStaleAuthMessage(msg)) {
          clearStaleCustomerAuthQuietly()
          reject(makeRequestError(msg, { code: 'AUTH_STALE' }))
          return
        }
        reject(makeRequestError(msg))
      }
    }
    if (upper !== 'GET') req.data = data
    my.request(req)
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
