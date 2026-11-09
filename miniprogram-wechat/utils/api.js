const { resolveApiBase, assertApiBaseConfigured } = require('../config/env')
const { localizeNativeErrMsg } = require('./errMsg')

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
  const configured = app && app.globalData
    ? String(app.globalData.apiBase || '').trim()
    : ''
  const base = configured || DEFAULT_API_BASE
  // 首次真实请求时校验；配置错误不会在 App 模块加载阶段造成白屏。
  assertApiBaseConfigured(base)
  if (app && app.globalData) app.globalData.apiBase = base
  return base
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

function formatValidationDetail(detail) {
  if (!Array.isArray(detail) || !detail.length) return ''
  const first = detail[0] || {}
  const loc = Array.isArray(first.loc) ? first.loc : []
  const field = loc.length ? String(loc[loc.length - 1]) : ''
  if (field === 'quantity' || /quantity/i.test(loc.join('.'))) {
    const max = first.ctx && first.ctx.le != null ? first.ctx.le : null
    return max != null ? `单品数量不能超过 ${max}` : '单品数量超出限制'
  }
  if (typeof first.msg === 'string' && first.msg) {
    return localizeNativeErrMsg(first.msg) || first.msg
  }
  return '请求参数有误'
}

function pickErrorMessage(resOrErr, fallback) {
  if (!resOrErr) return fallback
  if (typeof resOrErr === 'string') return localizeNativeErrMsg(resOrErr) || resOrErr
  const body = parseResponseBody(resOrErr.data)
  if (typeof resOrErr.data === 'string' && resOrErr.data.trim()) {
    const t = resOrErr.data.trim()
    return localizeNativeErrMsg(t) || t
  }
  if (body.message) return localizeNativeErrMsg(body.message) || body.message
  if (body.detail) {
    if (typeof body.detail === 'string') return localizeNativeErrMsg(body.detail) || body.detail
    const friendly = formatValidationDetail(body.detail)
    if (friendly) return friendly
    return JSON.stringify(body.detail)
  }
  const status = resOrErr.statusCode
  if (status != null && status >= 400) {
    return fallback || `请求失败(${status})`
  }
  const errMsg = resOrErr.errMsg
  if (errMsg && errMsg !== 'request:ok') return localizeNativeErrMsg(errMsg) || errMsg
  if (resOrErr.message) return localizeNativeErrMsg(resOrErr.message) || resOrErr.message
  return fallback
}

function networkHint(base, msg) {
  const raw = String(msg || '')
  const m = localizeNativeErrMsg(raw) || raw
  if (/合法域名|未放行请求域名/i.test(m)) return m
  if (/连接被重置/i.test(m)) {
    return '连接被重置：请确认后端已启动且未崩溃重启。开发：cd backend && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000'
  }
  if (/无法连接服务器|无法连接后端/i.test(m)) {
    return '无法连接后端，请先启动：cd backend && uvicorn app.main:app --host 0.0.0.0 --port 8000'
  }
  if (
    /127\.0\.0\.1|localhost/.test(base || '') &&
    /请求超时|网络|连接|失败|timeout|CONNECTION|reset|fail/i.test(raw + m)
  ) {
    return `${m}；真机请把 config/env.js 的 DEV_LAN_HOST 改为电脑局域网 IP（如 192.168.x.x），后端须 --host 0.0.0.0，并勾选「不校验合法域名」`
  }
  return m || '网络请求失败'
}

function isTransientNetworkError(msg) {
  return /CONNECTION_RESET|ERR_CONNECTION_RESET|reset by peer|CONNECTION_REFUSED|ECONNREFUSED|timeout/i.test(String(msg || ''))
}

function isStaleAuthMessage(msg) {
  return /顾客不存在|已禁用|无效顾客|请先登录|令牌已失效|无效令牌/.test(String(msg || ''))
}

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
  } catch (_) { }
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
      reject(makeRequestError('未配置 apiBase，请在 miniprogram-wechat/config/env.js 填写 PROD_API_BASE 或 DEV_LAN_HOST'))
      return
    }
    const upper = String(method || 'GET').toUpperCase()
    const urlPath = upper === 'GET' ? buildQueryUrl(path, data) : path
    const req = {
      url: `${base}${urlPath}`,
      method: upper,
      timeout: 20000,
      header: {
        'Content-Type': 'application/json; charset=utf-8',
        ...(needAuth ? authHeader() : {})
      },
      success(res) {
        const status = Number(res.statusCode)
        const body = parseResponseBody(res.data)
        let msg = pickErrorMessage(res, `请求失败(${status || '?'})`)
        // P2-9：500 类错误给用户友好提示，不暴露后端错误详情
        if (status && status >= 500) {
          msg = '服务器繁忙，请稍后重试'
        }
        const codeNum = body.code == null ? undefined : Number(body.code)
        if ((status && status >= 400) || (codeNum !== undefined && codeNum !== 0 && !Number.isNaN(codeNum))) {
          try {
            require('./logger').reportApi('ERROR', upper, path, { status, code: body.code, message: msg })
          } catch (_) { }
          // P1-3/P2-6：认证失效交给 request() 层先尝试静默刷新并重放，失败才清会话
          if (needAuth && (status === 401 || status === 403 || isStaleAuthMessage(msg))) {
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
        } catch (_) { }
        if (needAuth && isStaleAuthMessage(msg)) {
          reject(makeRequestError(msg, { code: 'AUTH_STALE' }))
          return
        }
        reject(makeRequestError(msg))
      }
    }
    if (upper !== 'GET') req.data = data
    wx.request(req)
  })
}

/** P2-6：静默刷新顾客令牌（单飞）。成功返回 true 并写回新 token，失败返回 false。 */
let refreshingPromise = null
function attemptTokenRefresh() {
  if (refreshingPromise) return refreshingPromise
  refreshingPromise = new Promise((resolve) => {
    const base = resolveBaseUrl()
    if (!base) {
      resolve(false)
      return
    }
    wx.request({
      url: `${base}/miniprogram/auth/refresh`,
      method: 'POST',
      timeout: 10000,
      header: {
        'Content-Type': 'application/json; charset=utf-8',
        ...authHeader()
      },
      success(res) {
        const body = parseResponseBody(res.data)
        const token = body && body.code === 0 && body.data ? body.data.customer_token : null
        if (Number(res.statusCode) === 200 && token) {
          try {
            const auth = require('./auth')
            const app = getAppSafe()
            if (app && typeof auth.refreshCustomerToken === 'function') {
              const patch = body.data.customer_id != null ? { customer_id: body.data.customer_id } : {}
              auth.refreshCustomerToken(app, token, patch)
              resolve(true)
              return
            }
          } catch (_) { }
          try { wx.setStorageSync('customer_token', token) } catch (_) { }
          resolve(true)
          return
        }
        resolve(false)
      },
      fail() { resolve(false) }
    })
  }).then((ok) => {
    refreshingPromise = null
    return ok
  })
  return refreshingPromise
}

function request(method, path, data = {}, needAuth = true, retries = 1, refreshed = false) {
  return requestOnce(method, path, data, needAuth).catch((err) => {
    // P2-6：令牌失效先静默刷新并重放一次（仅一次防循环；refresh 自身失效不重试）
    if (needAuth && !refreshed && err && err.code === 'AUTH_STALE' && !/auth\/refresh/.test(String(path))) {
      return attemptTokenRefresh().then((ok) => {
        if (ok) return request(method, path, data, needAuth, retries, true)
        clearStaleCustomerAuthQuietly()
        throw err
      })
    }
    // 仅对幂等的 GET 自动重试；POST/PUT/PATCH（下单/支付等）重试可能重复提交
    if (retries > 0 && method === 'GET' && isTransientNetworkError(err && err.message)) {
      // P3-1：指数退避 + 抖动，避免请求密集重试
      const attempt = 2 - retries // 0, 1
      const baseDelay = 400 * Math.pow(2, attempt) // 400ms, 800ms
      const jitter = Math.floor(Math.random() * 200) // 0~199ms 抖动
      return new Promise((resolve, reject) => {
        setTimeout(() => {
          request(method, path, data, needAuth, retries - 1, refreshed).then(resolve).catch(reject)
        }, baseDelay + jitter)
      })
    }
    if (err && err.code === 'AUTH_STALE') clearStaleCustomerAuthQuietly()
    throw err
  })
}

module.exports = {
  get: (path, data, needAuth = true) => request('GET', path, data, needAuth),
  post: (path, data, needAuth = true) => request('POST', path, data, needAuth),
  put: (path, data, needAuth = true) => request('PUT', path, data, needAuth),
  patch: (path, data, needAuth = true) => request('PATCH', path, data, needAuth),
  delete: (path, data, needAuth = true) => request('DELETE', path, data, needAuth)
}
