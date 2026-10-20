/** 餐品图片 URL 解析 + 按屏幕自适应尺寸 */

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
  // apiBase 形如 http://host:8000/api；uploads 挂在站点根，须去掉末尾 /api（兼容历史 /api/vN）
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

const _localImageCache = {}

/** 开发环境 HTTP 图：downloadFile 转本地临时路径（wx-image 不支持 HTTP） */
function ensureLocalImage(url) {
  const full = resolveUrl(url)
  if (!full) return Promise.resolve('')
  if (/^https:\/\//i.test(full)) return Promise.resolve(full)
  if (!/^http:\/\//i.test(full)) return Promise.resolve(full)
  if (_localImageCache[full]) return Promise.resolve(_localImageCache[full])

  const downloader = (typeof wx !== 'undefined' && wx.downloadFile)
    ? wx.downloadFile.bind(wx)
    : (typeof my !== 'undefined' && my.downloadFile)
      ? (opts) => my.downloadFile(opts)
      : null
  if (!downloader) return Promise.resolve(full)

  // 保留扩展名，避免 WebP 被存成无后缀临时文件导致客户端无法识别
  const extMatch = full.split('?')[0].match(/\.([a-z0-9]+)$/i)
  const ext = (extMatch && extMatch[1].toLowerCase()) || 'jpg'
  let filePath
  try {
    if (typeof wx !== 'undefined' && wx.env && wx.env.USER_DATA_PATH) {
      const hash = full.length.toString(36) + Math.abs(_hashStr(full)).toString(36)
      filePath = `${wx.env.USER_DATA_PATH}/media_${hash}.${ext}`
    }
  } catch (_) { }

  const downloadOnce = (withPath) => new Promise((resolve) => {
    const opts = {
      url: full,
      success: (res) => {
        const path = res.filePath || res.tempFilePath || res.apFilePath
        if (path && (!res.statusCode || res.statusCode === 200)) {
          _localImageCache[full] = path
          resolve(path)
        } else {
          resolve('')
        }
      },
      fail: () => resolve('')
    }
    if (withPath && filePath) opts.filePath = filePath
    downloader(opts)
  })

  return downloadOnce(true).then((path) => {
    if (path || !filePath) return path || full
    return downloadOnce(false).then((p) => p || full)
  })
}

function _hashStr(s) {
  let h = 0
  const str = String(s || '')
  for (let i = 0; i < str.length; i++) h = ((h << 5) - h) + str.charCodeAt(i) | 0
  return h
}

/** 限制并发，避免上百道菜同时 downloadFile 占满微信 10 路请求，把菜单接口挤超时。 */
async function mapPool(list, limit, worker) {
  const items = list || []
  const cap = Math.max(1, Math.min(limit || 4, items.length || 1))
  let index = 0
  async function run() {
    while (index < items.length) {
      const i = index
      index += 1
      await worker(items[i], i)
    }
  }
  const tasks = []
  for (let i = 0; i < cap && i < items.length; i++) tasks.push(run())
  await Promise.all(tasks)
  return items
}

/** 批量把列表项中的图片字段转为可展示的本地/HTTPS 地址 */
async function hydrateImageUrls(items, field = 'imageUrl') {
  const list = items || []
  await mapPool(list, 4, async (item) => {
    if (!item || !item[field]) return
    const original = item[field]
    const next = await ensureLocalImage(original)
    // 下载失败时保留原 URL，避免清空后只剩占位符
    item[field] = next || original
  })
  return list
}

/** 批量转换 URL 字符串列表（餐品详情轮播等） */
async function hydrateUrlList(urls) {
  const list = urls || []
  return Promise.all(list.map((u) => ensureLocalImage(u)))
}

/**
 * 按当前屏宽计算展示尺寸（逻辑像素 px）
 * 常见视口 360~414；列表缩略约屏宽 22%，详情头图约 1:1 全宽
 */
function readWindowWidth() {
  try {
    if (typeof wx !== 'undefined' && wx.getWindowInfo) {
      return wx.getWindowInfo().windowWidth
    }
    if (typeof wx !== 'undefined' && wx.getSystemInfoSync) {
      return wx.getSystemInfoSync().windowWidth
    }
    if (typeof my !== 'undefined' && my.getSystemInfoSync) {
      return my.getSystemInfoSync().windowWidth
    }
  } catch (_) {}
  return 375
}

function layoutMetrics(sys) {
  const w = (sys && (sys.windowWidth || sys.screenWidth)) || readWindowWidth()

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

/** 给餐品列表项补 imageUrl / cover */
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
