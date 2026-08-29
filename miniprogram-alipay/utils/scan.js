/** 解析扫码结果 / 小程序码 scene 解析桌台 token */

function parseTableTokenFromScan(raw) {
  if (!raw) return ''
  const s = String(raw).trim()
  if (!s) return ''

  if (!s.includes('/') && !s.includes('?') && !s.includes('&') && !s.includes('=')) {
    return s
  }

  try {
    const tokenMatch = s.match(/[?&]token=([^&]+)/i)
    if (tokenMatch && tokenMatch[1]) return decodeURIComponent(tokenMatch[1])

    if (s.includes('token=')) {
      const pair = s.split('&').find((p) => p.trim().toLowerCase().startsWith('token='))
      if (pair) return decodeURIComponent(pair.split('=').slice(1).join('=') || '')
    }

    if (s.includes('=')) {
      let found = ''
      s.split('&').forEach((pair) => {
        const [k, ...rest] = pair.split('=')
        if (k === 'token' && rest.length) found = decodeURIComponent(rest.join('='))
      })
      if (found) return found
    }
  } catch (_) {}

  return s
}

module.exports = {
  parseTableTokenFromScan
}
