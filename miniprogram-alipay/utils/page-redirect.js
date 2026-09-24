/** 旧主包页面路径 → 分包页面（兼容历史链接与开发者工具缓存） */

const ORDER_SUBPACKAGE = 'order'
const FALLBACK_TAB = '/pages/menu/menu'

function buildQuery(query) {
  if (!query || typeof query !== 'object') return ''
  const parts = Object.keys(query)
    .filter((k) => query[k] != null && query[k] !== '')
    .map((k) => `${encodeURIComponent(k)}=${encodeURIComponent(query[k])}`)
  return parts.length ? `?${parts.join('&')}` : ''
}

function goFallback() {
  my.switchTab({
    url: FALLBACK_TAB,
    fail: () => my.reLaunch({ url: '/pages/index/index' })
  })
}

function navigateToUrl(url) {
  if (!url || typeof url !== 'string' || !url.trim()) {
    goFallback()
    return
  }
  const target = url.trim()
  my.redirectTo({
    url: target,
    fail: () => {
      my.reLaunch({
        url: target,
        fail: goFallback
      })
    }
  })
}

function loadOrderSubpackage(done) {
  if (typeof my.loadSubPackage !== 'function' && typeof my.loadSubpackage !== 'function') {
    done()
    return
  }
  let settled = false
  const finish = () => {
    if (settled) return
    settled = true
    done()
  }
  try {
    const loader = my.loadSubPackage || my.loadSubpackage
    loader({
      name: ORDER_SUBPACKAGE,
      success: finish,
      fail: finish,
      complete: finish
    })
  } catch (_) {
    finish()
  }
  setTimeout(finish, 1200)
}

/**
 * 主包兼容页跳转到分包。须等当前页渲染完成且分包就绪，
 * 否则热重载/首屏易出现 page "" is not found。
 */
function redirectToPackage(path, query) {
  const base = String(path || '').trim()
  if (!base) {
    goFallback()
    return
  }
  const url = `${base.startsWith('/') ? base : `/${base}`}${buildQuery(query)}`
  const go = () => loadOrderSubpackage(() => navigateToUrl(url))
  setTimeout(go, 0)
}

module.exports = {
  buildQuery,
  redirectToPackage,
  FALLBACK_TAB
}
