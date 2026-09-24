/** 兼容旧路径：跳转分包确认页（与微信 pages/confirm stub 对齐） */
Page({
  onLoad(query) {
    const parts = []
    if (query && typeof query === 'object') {
      Object.keys(query).forEach((k) => {
        if (query[k] == null || query[k] === '') return
        parts.push(`${encodeURIComponent(k)}=${encodeURIComponent(query[k])}`)
      })
    }
    const qs = parts.length ? `?${parts.join('&')}` : ''
    const url = `/packageOrder/confirm/confirm${qs}`
    my.redirectTo({
      url,
      fail: () => {
        my.reLaunch({
          url,
          fail: () => my.switchTab({ url: '/pages/menu/menu' })
        })
      }
    })
  }
})
