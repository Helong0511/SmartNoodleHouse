/**
 * 顾客行为埋点 SDK（智能推荐 Task 4）
 * 事件：EXPOSE / CLICK / CART_ADD / CART_REMOVE / ORDER_PAID / REORDER / FEEDBACK
 * - 本地队列持久化，批量上报（每 10 条或 15 秒），App onHide 时 flush
 * - event_id 客户端生成，服务端幂等去重；未登录时暂存不发，登录后自动补发
 * - 关闭个性化推荐（opt_in=0）由服务端丢弃，前端仍正常记录行为流
 */
const api = require('./api')

const QUEUE_KEY = 'behavior_event_queue'
const MAX_QUEUE = 100
const BATCH_SIZE = 50
const FLUSH_SIZE = 10
const FLUSH_INTERVAL = 15000
const VALID_TYPES = ['EXPOSE', 'CLICK', 'CART_ADD', 'CART_REMOVE', 'ORDER_PAID', 'REORDER', 'FEEDBACK']

let seq = 0
let flushing = false
let lastFlushAt = 0

function getAppSafe() {
  try {
    return getApp()
  } catch (_) {
    return null
  }
}

function genEventId() {
  seq += 1
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}-${seq}`
}

function readQueue() {
  try {
    return wx.getStorageSync(QUEUE_KEY) || []
  } catch (_) {
    return []
  }
}

function writeQueue(queue) {
  try {
    wx.setStorageSync(QUEUE_KEY, queue)
  } catch (_) {}
}

function hasToken() {
  try {
    return !!wx.getStorageSync('customer_token')
  } catch (_) {
    return false
  }
}

function currentStoreId() {
  const app = getAppSafe()
  const store = app && app.globalData && app.globalData.store
  return store && store.id ? store.id : null
}

function pruneQueue(queue) {
  // 超容量优先丢弃最旧的低权重曝光/点击事件，保留下单等强信号
  let q = queue
  while (q.length > MAX_QUEUE) {
    const idx = q.findIndex((e) => e.event_type === 'EXPOSE' || e.event_type === 'CLICK')
    if (idx < 0) {
      q = q.slice(q.length - MAX_QUEUE)
      break
    }
    q = q.slice(0, idx).concat(q.slice(idx + 1))
  }
  return q
}

function track(eventType, props) {
  const type = String(eventType || '').toUpperCase()
  if (VALID_TYPES.indexOf(type) < 0) return
  const p = props || {}
  const event = {
    event_id: p.event_id || genEventId(),
    event_type: type,
    scene: p.scene ? String(p.scene).slice(0, 20) : null,
    product_id: p.product_id || null,
    category_id: p.category_id || null,
    store_id: p.store_id || currentStoreId() || null,
    ab_bucket: p.ab_bucket ? String(p.ab_bucket).slice(0, 80) : null,
    payload: p.payload || null,
    occurred_at: new Date().toISOString()
  }
  const queue = pruneQueue(readQueue().concat([event]))
  writeQueue(queue)
  const now = Date.now()
  if (queue.length >= FLUSH_SIZE || now - lastFlushAt > FLUSH_INTERVAL) {
    flush()
  }
}

function flush() {
  if (flushing) return Promise.resolve({ skipped: true })
  const queue = readQueue()
  if (!queue.length) return Promise.resolve({ skipped: true })
  if (!hasToken()) return Promise.resolve({ skipped: true, reason: 'no_token' })

  flushing = true
  const batch = queue.slice(0, BATCH_SIZE)
  const batchIds = {}
  batch.forEach((e) => { batchIds[e.event_id] = true })

  return api
    .post('/miniprogram/events', { events: batch }, true)
    .then(() => {
      lastFlushAt = Date.now()
      const rest = readQueue().filter((e) => !batchIds[e.event_id])
      writeQueue(rest)
      if (rest.length >= FLUSH_SIZE) {
        setTimeout(() => flush(), 300)
      }
      return { sent: batch.length, remaining: rest.length }
    })
    .catch(() => {
      // 网络失败/401：保留队列，下次 onShow 或 track 时重试
      return { sent: 0, remaining: readQueue().length }
    })
    .finally(() => {
      flushing = false
    })
}

module.exports = {
  track,
  flush,
  expose: (productId, extra) => track('EXPOSE', Object.assign({ product_id: productId }, extra || {})),
  click: (productId, extra) => track('CLICK', Object.assign({ product_id: productId }, extra || {})),
  cartAdd: (productId, extra) => track('CART_ADD', Object.assign({ product_id: productId }, extra || {})),
  cartRemove: (productId, extra) => track('CART_REMOVE', Object.assign({ product_id: productId }, extra || {})),
  reorder: (extra) => track('REORDER', extra || {}),
  feedback: (productId, extra) => track('FEEDBACK', Object.assign({ product_id: productId }, extra || {}))
}
