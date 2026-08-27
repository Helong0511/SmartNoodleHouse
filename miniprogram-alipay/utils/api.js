const app = getApp()

function authHeader() {
  const token =
    (app && app.globalData && app.globalData.customerToken) ||
    (my.getStorageSync({ key: 'customer_token' }) || {}).data
  return token ? { Authorization: `Bearer ${token}` } : {}
}

function pickErrorMessage(resOrErr, fallback) {
  if (!resOrErr) return fallback
  if (typeof resOrErr === 'string') return resOrErr
  const body = resOrErr.data
  if (typeof body === 'string' && body.trim()) return body.trim()
  if (body && typeof body === 'object') {
    if (body.message) return body.message
    if (body.detail) {
      return typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    }
  }
  if (resOrErr.status >= 400 || resOrErr.statusCode >= 400) {
    const code = resOrErr.status || resOrErr.statusCode
    return fallback || `请求失败(${code})`
  }
  const errMsg = resOrErr.errorMessage || resOrErr.errMsg || resOrErr.message
  if (errMsg) return errMsg
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
    return `${m}；真机请把 app.js 的 apiBase 改为电脑局域网 IP（如 http://192.168.x.x:8000/api/v1）`
  }
  return m || '网络请求失败'
}

function request(method, path, data = {}, needAuth = true) {
  const base = (app && app.globalData && app.globalData.apiBase) || ''
  return new Promise((resolve, reject) => {
    if (!base) {
      reject(new Error('未配置 apiBase，请在 app.js 填写后端地址'))
      return
    }
    my.request({
      url: `${base}${path}`,
      method,
      data,
      timeout: 20000,
      headers: {
        'Content-Type': 'application/json',
        ...(needAuth ? authHeader() : {})
      },
      success(res) {
        const body = res.data || {}
        const status = res.status != null ? res.status : res.statusCode
        if (status >= 400) {
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

module.exports = {
  get: (path, data, needAuth = true) => request('GET', path, data, needAuth),
  post: (path, data, needAuth = true) => request('POST', path, data, needAuth),
  put: (path, data, needAuth = true) => request('PUT', path, data, needAuth),
  patch: (path, data, needAuth = true) => request('PATCH', path, data, needAuth)
}
