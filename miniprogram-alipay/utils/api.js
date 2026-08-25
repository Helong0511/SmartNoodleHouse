const app = getApp()

function authHeader() {
  const token = app.globalData.customerToken || (my.getStorageSync({ key: 'customer_token' }).data)
  return token ? { Authorization: `Bearer ${token}` } : {}
}

function request(method, path, data = {}, needAuth = true) {
  return new Promise((resolve, reject) => {
    my.request({
      url: `${app.globalData.apiBase}${path}`,
      method,
      data,
      headers: {
        'Content-Type': 'application/json',
        ...(needAuth ? authHeader() : {})
      },
      success(res) {
        const body = res.data || {}
        if (body.code !== 0 && body.code !== undefined) {
          reject(new Error(body.message || '请求失败'))
          return
        }
        resolve(body.data)
      },
      fail: reject
    })
  })
}

module.exports = {
  get: (path, data, needAuth = true) => request('GET', path, data, needAuth),
  post: (path, data, needAuth = true) => request('POST', path, data, needAuth)
}
