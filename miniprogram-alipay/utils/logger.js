/** 支付宝小程序全部日志 → POST /api/client-logs → logs/alipay/ */
const SOURCE = 'alipay'
const FLUSH_MS = 5000
/** 与后端 client_logs._MAX_ENTRIES / _MAX_MESSAGE 对齐 */
const MAX_BATCH = 30
const MAX_MESSAGE = 2000
const MAX_QUEUE = 500
let flushFailures = 0

const queue = []
let flushTimer = null
let flushing = false
let installed = false
let reporting = false
const originalConsole = {}

function nowIso() {
  try {
    return new Date().toISOString()
  } catch (_) {
    return ''
  }
}

function currentPage() {
  try {
    const pages = getCurrentPages()
    if (!pages || !pages.length) return ''
    const cur = pages[pages.length - 1]
    return (cur && (cur.route || cur.__route__)) || ''
  } catch (_) {
    return ''
  }
}

function stringifyArg(v) {
  if (v == null) return String(v)
  if (typeof v === 'string') return v
  if (v instanceof Error) return `${v.name}: ${v.message}`
  try {
    return JSON.stringify(v)
  } catch (_) {
    return String(v)
  }
}

function safeExtra(v) {
  try {
    if (v == null) return undefined
    if (typeof v === 'string') return v.slice(0, 500)
    if (v instanceof Error) return { name: v.name, message: v.message }
    return JSON.parse(JSON.stringify(v))
  } catch (_) {
    return String(v).slice(0, 500)
  }
}

function remoteReportingAllowed() {
  // 远程客户端日志只允许开发/体验环境；正式版即使误调用 reportApi 也不得外发。
  try {
    return !require('../config/env').isRelease()
  } catch (_) {
    return false
  }
}

function push(level, message, extra) {
  if (!remoteReportingAllowed() || reporting) return
  const msg = String(message || '').slice(0, MAX_MESSAGE)
  if (!msg) return
  queue.push({
    level,
    message: msg,
    ts: nowIso(),
    page: currentPage(),
    extra: extra === undefined ? undefined : safeExtra(extra)
  })
  while (queue.length > MAX_QUEUE) queue.shift()
  scheduleFlush()
}

function scheduleFlush() {
  if (flushTimer) return
  flushTimer = setTimeout(() => {
    flushTimer = null
    flush()
  }, FLUSH_MS)
  if (queue.length >= MAX_BATCH) {
    clearTimeout(flushTimer)
    flushTimer = null
    flush()
  }
}

function flush() {
  if (!remoteReportingAllowed()) {
    queue.length = 0
    if (flushTimer) clearTimeout(flushTimer)
    flushTimer = null
    return
  }
  if (flushing || !queue.length) return
  flushing = true
  const batch = queue.splice(0, MAX_BATCH)
  let api
  try {
    api = require('./api')
  } catch (_) {
    queue.unshift(...batch)
    flushing = false
    return
  }
  reporting = true
  api
    .post('/client-logs', { source: SOURCE, entries: batch }, false)
    .then(() => {
      flushFailures = 0
    })
    .catch((err) => {
      // 校验类/权限类错误丢弃本批，避免 unshift 死循环打限流
      const status = err && (err.status || err.statusCode || err.code)
      const isValidation =
        status === 422 ||
        status === 401 ||
        status === 403 ||
        status === 40001 ||
        (err && /参数|校验|过大|最多/.test(String(err.message || '')))
      if (isValidation) {
        flushFailures = 0
        return
      }
      flushFailures++
      if (flushFailures < 3) {
        queue.unshift(...batch)
        while (queue.length > MAX_QUEUE) queue.pop()
      } else {
        flushFailures = 0
      }
    })
    .then(() => {
      reporting = false
      flushing = false
      if (queue.length) scheduleFlush()
    })
}

function hookConsole() {
  const map = { debug: 'DEBUG', log: 'INFO', info: 'INFO', warn: 'WARN', error: 'ERROR' }
  Object.keys(map).forEach((key) => {
    const orig = console[key]
    if (typeof orig !== 'function') return
    originalConsole[key] = orig.bind(console)
    console[key] = function (...args) {
      try {
        push(map[key], args.map(stringifyArg).join(' '))
      } catch (_) {}
      return originalConsole[key](...args)
    }
  })
}

function reportApi(level, method, path, detail) {
  if (!remoteReportingAllowed() || String(path || '').indexOf('/client-logs') >= 0) return
  // 避免将动态订单号、支付单号或 query 参数写入客户端日志。
  const safePath = String(path || '').split('?')[0].replace(/\/(orders|payments|refunds|recharges)\/[^/]+/g, '/$1/:id').slice(0, 120)
  const safeDetail = detail && typeof detail === 'object'
    ? { status: detail.status, code: detail.code }
    : undefined
  push(level, `api ${String(method || '').toUpperCase()} ${safePath}`, safeDetail)
}

const logger = {
  debug: (m, extra) => push('DEBUG', m, extra),
  info: (m, extra) => push('INFO', m, extra),
  warn: (m, extra) => push('WARN', m, extra),
  error: (m, extra) => push('ERROR', m, extra),
  flush
}

function install() {
  if (installed) return
  installed = true
  hookConsole()
  try {
    if (typeof my !== 'undefined' && typeof my.onError === 'function') {
      my.onError((err) => {
        const msg = (err && (err.message || err.errorMessage || err)) || 'my.onError'
        push('ERROR', String(msg), err)
      })
    }
  } catch (_) {}
  try {
    if (typeof my !== 'undefined' && typeof my.onUnhandledRejection === 'function') {
      my.onUnhandledRejection((res) => {
        const reason = (res && (res.reason || res.message)) || 'unhandledrejection'
        push('ERROR', String(reason), res)
      })
    }
  } catch (_) {}
}

module.exports = { logger, install, push, reportApi }
