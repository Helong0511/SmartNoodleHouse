/** 解析扫码结果 / Scheme / 小程序启动参数中的桌台 token */

function safeDecode(s) {
  let cur = String(s || '')
  for (let i = 0; i < 3; i += 1) {
    if (!/%[0-9A-Fa-f]{2}/.test(cur)) break
    try {
      const next = decodeURIComponent(cur)
      if (next === cur) break
      cur = next
    } catch (_) {
      break
    }
  }
  return cur
}

function stripHash(s) {
  const i = s.indexOf('#')
  return i >= 0 ? s.slice(0, i) : s
}

/** 去掉 hash 后再拼回 hash 内的查询串（兼容 /#/scan?token=） */
function flattenUrlForToken(s) {
  const raw = String(s || '')
  const hashIdx = raw.indexOf('#')
  if (hashIdx < 0) return raw
  const before = raw.slice(0, hashIdx)
  let after = raw.slice(hashIdx + 1)
  if (after.startsWith('/')) after = after.slice(1)
  // #/scan?token=x → 用 ? 拼接便于 pickToken
  if (after.includes('token=') || after.includes('?')) {
    const q = after.includes('?') ? after.slice(after.indexOf('?')) : `?${after}`
    return `${before}${q.startsWith('?') ? q : `?${q}`}`
  }
  return before
}

/** 把启动 options.query（对象或 token=a&channel=b 字符串）归一成对象 */
function normalizeLaunchQuery(query) {
  if (query == null || query === '') return {}
  if (typeof query === 'object' && !Array.isArray(query)) {
    const out = { ...query }
    // 个别机型把整段 query 又塞进 query 字段
    if (typeof out.query === 'string' && !out.token) {
      Object.assign(out, normalizeLaunchQuery(out.query))
    }
    if (out.token != null) out.token = safeDecode(String(out.token)).trim()
    if (out.channel != null) out.channel = safeDecode(String(out.channel)).trim()
    return out
  }
  if (typeof query !== 'string') return {}
  const s = safeDecode(stripHash(query.trim()))
  if (!s) return {}
  // 纯 token
  if (!s.includes('=') && !s.includes('&') && !s.includes('?')) {
    return { token: s }
  }
  const out = {}
  const body = s.startsWith('?') ? s.slice(1) : s
  body.split('&').forEach((pair) => {
    const idx = pair.indexOf('=')
    if (idx < 0) return
    const k = safeDecode(pair.slice(0, idx)).trim()
    const v = safeDecode(pair.slice(idx + 1)).trim()
    if (k) out[k] = v
  })
  return out
}

function pickTokenFromPairs(s) {
  const raw = stripHash(String(s || ''))
  if (!raw) return ''

  const tokenMatch = raw.match(/(?:^|[?&])token=([^&]*)/i)
  if (tokenMatch && tokenMatch[1]) {
    return safeDecode(tokenMatch[1]).trim()
  }

  if (raw.includes('token=')) {
    const pair = raw.split('&').find((p) => p.trim().toLowerCase().startsWith('token='))
    if (pair) return safeDecode(pair.split('=').slice(1).join('=') || '').trim()
  }
  return ''
}

/**
 * 从扫码字符串解析桌台 token。
 * 兼容：纯 token、/scan?token=、pages/...?token=、
 * weixin/alipays Scheme（token 藏在 URL 编码的 query= 里）
 */
function parseTableTokenFromScan(raw) {
  if (!raw) return ''
  let s = String(raw).trim()
  if (!s) return ''
  s = flattenUrlForToken(s)
  s = stripHash(s)

  // 纯 token（开发环境桌号等）
  if (!s.includes('/') && !s.includes('?') && !s.includes('&') && !s.includes('=')) {
    return s
  }

  try {
    let found = pickTokenFromPairs(s)
    if (found) return found

    // Scheme：...&query=token%3Dxxx%26channel%3Dalipay
    const queryMatch = s.match(/[?&]query=([^&]+)/i)
    if (queryMatch && queryMatch[1]) {
      const inner = safeDecode(queryMatch[1])
      found = pickTokenFromPairs(inner)
      if (found) return found
      const nested = normalizeLaunchQuery(inner)
      if (nested.token) return String(nested.token).trim()
    }

    // page=pages/index/index%3Ftoken%3Dxxx 或带 ?token=
    const pageMatch = s.match(/[?&]page=([^&]+)/i)
    if (pageMatch && pageMatch[1]) {
      const page = safeDecode(pageMatch[1])
      found = pickTokenFromPairs(page)
      if (found) return found
      const qIdx = page.indexOf('?')
      if (qIdx >= 0) {
        const nested = normalizeLaunchQuery(page.slice(qIdx + 1))
        if (nested.token) return String(nested.token).trim()
      }
    }

    // path= 同理（微信 Scheme）
    const pathMatch = s.match(/[?&]path=([^&]+)/i)
    if (pathMatch && pathMatch[1]) {
      const path = safeDecode(pathMatch[1])
      found = pickTokenFromPairs(path)
      if (found) return found
    }
  } catch (_) {}

  // 整段 URL/Scheme 不能当 token，避免误报「无效桌台二维码」
  if (/^(https?:|alipays?:|weixin:|wechat:)\/\//i.test(s) || s.includes('://')) {
    return ''
  }
  return s
}

module.exports = {
  parseTableTokenFromScan,
  normalizeLaunchQuery,
  safeDecode
}
