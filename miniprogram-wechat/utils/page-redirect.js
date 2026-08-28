/** 旧主包页面路径 → 分包页面（兼容历史链接与开发者工具缓存） */
function buildQuery(query) {
  if (!query || typeof query !== 'object') return ''
  const parts = Object.keys(query)
    .filter((k) => query[k] != null && query[k] !== '')
    .map((k) => `${encodeURIComponent(k)}=${encodeURIComponent(query[k])}`)
  return parts.length ? `?${parts.join('&')}` : ''
}

function redirectToPackage(path, query) {
  const url = `${path}${buildQuery(query)}`
  const go = () => {
    wx.redirectTo({
      url,
      fail: () => wx.reLaunch({ url })
    })
  }
  // 延迟到当前页渲染完成后再跳转，避免热重载/首屏时 routeDone webview 竞态
  if (typeof wx.nextTick === 'function') {
    wx.nextTick(go)
  } else {
    setTimeout(go, 0)
  }
}

module.exports = {
  buildQuery,
  redirectToPackage
}
