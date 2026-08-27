const app = getApp()

function authHeader() {
  const token = wx.getStorageSync('customer_token') || (app && app.globalData && app.globalData.customerToken)
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
  if (resOrErr.statusCode >= 400) {
    return fallback || `请求失败(${resOrErr.statusCode})`
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
    return `${m}；真机/部分模拟器请把 app.js 的 apiBase 改为电脑局域网 IP（如 http://192.168.x.x:8000/api/v1），并勾选「不校验合法域名」`
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
    wx.request({
      url: `${base}${path}`,
      method,
      data,
      timeout: 20000,
      header: {
        'Content-Type': 'application/json',
        ...(needAuth ? authHeader() : {})
      },
      success(res) {
        const body = res.data || {}
        if (res.statusCode >= 400) {
          reject(new Error(pickErrorMessage(res, `请求失败(${res.statusCode})`)))
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
