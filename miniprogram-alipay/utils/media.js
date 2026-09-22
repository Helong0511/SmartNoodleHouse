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
  // apiBase 形如 http://host:8000/api 或 .../api/v1；uploads 挂在站点根，须去掉 /api 前缀
  return String(base).replace(/\/api(?:\/v\d+)?\/?$/i, '')
}

/** 相对路径 /uploads/... → 完整 URL；已是 http(s) 则原样返回；version 用于破除小程序/Nginx 缓存 */
function resolveUrl(path, version) {
  if (!path) return ''
  const s = String(path).trim()
  if (!s) return ''
  let url = s
  if (!/^https?:\/\//i.test(s)) {
    const origin = apiOrigin()
    if (!origin) url = s.startsWith('/') ? s : `/${s}`
    else url = s.startsWith('/') ? `${origin}${s}` : `${origin}/${s}`
  }
  const v = version != null && version !== '' ? String(version) : ''
  if (v && !/[?&]v=/.test(url)) {
    url += `${url.includes('?') ? '&' : '?'}v=${encodeURIComponent(v)}`
  }
  return url
}

function imageVersion(source) {
  if (!source || typeof source !== 'object') return ''
  return source.updated_at || source.image_version || ''
}

/** 补全为可展示地址；支付宝 <image> 支持 HTTP，开发版可忽略域名校验 */
function ensureLocalImage(url) {
  const full = resolveUrl(url)
  return Promise.resolve(full || '')
}

async function hydrateImageUrls(items, field = 'imageUrl') {
  const list = items || []
  await Promise.all(list.map(async (item) => {
    if (!item || !item[field]) return
    item[field] = await ensureLocalImage(item[field])
  }))
  return list
}

/** 批量转换 URL 字符串列表（商品详情轮播等） */
async function hydrateUrlList(urls) {
  const list = urls || []
  return Promise.all(list.map((u) => ensureLocalImage(u)))
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
  const version = imageVersion(p)
  const raw = (p && (p.main_image || p.image_url)) || ''
  const imageUrl = resolveUrl(raw, version)
  const cover = raw
  return {
    ...p,
    description: (p && p.description) || '',
    imageUrl,
    coverUrl: resolveUrl(cover, version),
    thumbStyle: (metrics && metrics.thumbStyle) || ''
  }
}

function galleryUrls(product) {
  const list = []
  const seen = {}
  const version = imageVersion(product)
  const push = (u) => {
    const full = resolveUrl(u, version)
    if (full && !seen[full]) {
      seen[full] = true
      list.push(full)
    }
  }
  if (product) {
    push(product.main_image)
    ;(product.images || []).forEach((img) => {
      if (img && img.type === 'MAIN') return
      push(img.url || img.image_url)
    })
  }
  return list
}

module.exports = {
  resolveUrl,
  imageVersion,
  ensureLocalImage,
  hydrateImageUrls,
  hydrateUrlList,
  layoutMetrics,
  decorateProduct,
  galleryUrls
}
