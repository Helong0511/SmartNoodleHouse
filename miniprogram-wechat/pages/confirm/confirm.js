function buildQuery(query) {
  if (!query || typeof query !== 'object') return ''
  const parts = Object.keys(query)
    .filter((k) => query[k] != null && query[k] !== '')
    .map((k) => `${encodeURIComponent(k)}=${encodeURIComponent(query[k])}`)
  return parts.length ? `?${parts.join('&')}` : ''
}

Page({
  onLoad(query) {
    const url = `/packageOrder/confirm/confirm${buildQuery(query)}`
    wx.redirectTo({ url, fail: () => wx.reLaunch({ url }) })
  }
})
