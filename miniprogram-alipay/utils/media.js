/** 商品图片 URL 解析 + 按屏幕自适应尺寸 */

function getAppSafe() {
  try {
    return getApp()
  } catch (_) {
    return null
  }
}

function apiOrigin() {
  const app = getAppSafe()
  const base = (app && app.globalData && app.globalData.apiBase) || ''
  return String(base).replace(/\/api\/v1\/?$/i, '')
}

/** 相对路径 /uploads/... → 完整 URL；已是 http(s) 则原样返回 */
function resolveUrl(path) {
  if (!path) return ''
  const s = String(path).trim()
  if (!s) return ''
  if (/^https?:\/\//i.test(s)) return s
  const origin = apiOrigin()
  if (!origin) return s.startsWith('/') ? s : `/${s}`
  return s.startsWith('/') ? `${origin}${s}` : `${origin}/${s}`
}

/**
 * 按当前屏宽计算展示尺寸（逻辑像素 px）
 * 常见视口 360~414；列表缩略约屏宽 22%，详情头图约 1:1 全宽
 */
function layoutMetrics(sys) {
  let w = 375
  try {
    const info = sys || (typeof wx !== 'undefined' && wx.getSystemInfoSync
      ? wx.getSystemInfoSync()
      : (typeof my !== 'undefined' && my.getSystemInfoSync ? my.getSystemInfoSync() : null))
    w = (info && (info.windowWidth || info.screenWidth)) || 375
  } catch (_) {}

  const isWide = w >= 600
  // 列表缩略：窄屏 72~100，宽屏/Pad 略大
  const thumbPx = Math.round(Math.min(isWide ? 140 : 110, Math.max(72, w * 0.22)))
  // 详情头图高度：手机约全宽正方形，宽屏略扁 4:3
  const heroHeightPx = Math.round(isWide ? w * 0.55 : w)
  return {
    windowWidth: w,
    isWide,
    thumbPx,
    thumbStyle: `width:${thumbPx}px;height:${thumbPx}px;`,
    heroHeightPx,
    heroStyle: `height:${heroHeightPx}px;`
  }
}

/** 给商品列表项补 imageUrl / cover */
function decorateProduct(p, metrics) {
  const raw = (p && (p.main_image || p.image_url)) || ''
  const imageUrl = resolveUrl(raw)
  const cover = (p && p.images && p.images[0] && p.images[0].url) || raw
  return {
    ...p,
    description: (p && p.description) || '',
    imageUrl,
    coverUrl: resolveUrl(cover),
    thumbStyle: (metrics && metrics.thumbStyle) || ''
  }
}

function galleryUrls(product) {
  const list = []
  const seen = {}
  const push = (u) => {
    const full = resolveUrl(u)
    if (full && !seen[full]) {
      seen[full] = true
      list.push(full)
    }
  }
  if (product) {
    push(product.main_image)
    ;(product.images || []).forEach((img) => push(img.url || img.image_url))
  }
  return list
}

module.exports = {
  resolveUrl,
  layoutMetrics,
  decorateProduct,
  galleryUrls
}
