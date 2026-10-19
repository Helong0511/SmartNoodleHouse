/**
 * 同桌共享购物车客户端。
 * 规则：一人结全桌；只能改自己的行；桌主可清车；revision 乐观锁。
 * 需门店开启 shared_cart_enabled；堂食绑桌后启用；外带/未绑桌仍走 auth 本地 cart。
 */
const api = require('./api')
const auth = require('./auth')
const dineCtx = require('./context')
const clientConfig = require('./clientConfig')

let _mirror = []
let _meta = {
  enabled: false,
  revision: 0,
  is_host: false,
  party_id: null,
  member_count: 0
}
let _pollTimer = null
const _listeners = []

function tableParams() {
  const cached = dineCtx.readCachedContext ? dineCtx.readCachedContext() : {}
  const table = (cached && cached.table) || (dineCtx.currentDisplay() || {}).table || {}
  const token = String((cached && cached.token) || table.qr_token || '').trim()
  const params = {}
  if (token) params.table_token = token
  else if (table.id) params.table_id = table.id
  return params
}

function featureEnabled() {
  return !!clientConfig.boolFlag('shared_cart_enabled', false)
}

function shouldUseShared() {
  const ctx = dineCtx.currentDisplay() || {}
  return !!(ctx.hasTable && !ctx.isTakeawayChannel && featureEnabled())
}

function isActive() {
  return shouldUseShared() && !!_meta.enabled
}

function getMirror() {
  return (_mirror || []).map((x) => ({ ...x }))
}

function getMeta() {
  return { ..._meta }
}

function subscribe(fn) {
  if (typeof fn !== 'function') return function () {}
  _listeners.push(fn)
  return function () {
    const i = _listeners.indexOf(fn)
    if (i >= 0) _listeners.splice(i, 1)
  }
}

function notify() {
  _listeners.slice().forEach((fn) => {
    try { fn(getMirror(), getMeta()) } catch (_) { /* ignore */ }
  })
}

function applyPayload(payload) {
  if (!payload || payload.enabled === false) {
    _meta = {
      enabled: false,
      revision: 0,
      is_host: false,
      party_id: null,
      member_count: 0
    }
    _mirror = []
    notify()
    return _meta
  }
  _meta = {
    enabled: true,
    revision: Number(payload.revision) || 0,
    is_host: !!payload.is_host,
    party_id: payload.party_id || null,
    member_count: Number(payload.member_count) || 0
  }
  _mirror = (payload.items || []).map((it) => ({
    product_id: it.product_id,
    name: it.name,
    quantity: it.quantity || 1,
    unit_price: it.unit_price,
    subtotal: it.subtotal,
    option_ids: it.option_ids || [],
    option_text: it.option_text || '',
    option_labels: it.option_labels || [],
    category_id: it.category_id,
    main_image: it.main_image || '',
    imageUrl: it.imageUrl || '',
    no_member_discount: !!it.no_member_discount,
    remark: it.remark || '',
    shared_item_id: it.shared_item_id || it.id,
    id: it.shared_item_id || it.id,
    added_by_customer_id: it.added_by_customer_id,
    added_by_label: it.added_by_label || '',
    mine: !!it.mine,
    can_edit: it.can_edit !== false && !!it.mine
  }))
  // 同步镜像到本地 storage，便于结算页离线回退读
  try {
    auth.saveCart(_mirror)
  } catch (_) { /* ignore */ }
  notify()
  return _meta
}

async function joinAndPull() {
  try {
    await clientConfig.loadClientConfig(false)
  } catch (_) { /* ignore */ }
  if (!shouldUseShared()) {
    applyPayload({ enabled: false })
    return getMeta()
  }
  try {
    await auth.ensureLogin()
    const res = await api.post('/miniprogram/table-party/join', tableParams())
    applyPayload(res)
  } catch (e) {
    try {
      await pull()
    } catch (_) {
      applyPayload({ enabled: false })
    }
  }
  return getMeta()
}

async function pull() {
  try {
    await clientConfig.loadClientConfig(false)
  } catch (_) { /* ignore */ }
  if (!shouldUseShared()) {
    applyPayload({ enabled: false })
    return getMeta()
  }
  await auth.ensureLogin()
  const res = await api.get('/miniprogram/table-party/cart', tableParams())
  applyPayload(res)
  return getMeta()
}

async function addItem(entry) {
  await auth.ensureLogin()
  const params = tableParams()
  const body = {
    ...params,
    product_id: Number(entry.product_id),
    quantity: Number(entry.quantity) || 1,
    option_ids: entry.option_ids || [],
    option_text: entry.option_text || '',
    remark: entry.remark || '',
    unit_price: entry.unit_price != null ? String(entry.unit_price) : undefined,
    expected_revision: _meta.revision || undefined
  }
  const res = await api.post('/miniprogram/table-party/cart/items', body)
  applyPayload(res)
  return getMirror()
}

async function setQuantity(sharedItemId, quantity) {
  await auth.ensureLogin()
  const res = await api.patch(`/miniprogram/table-party/cart/items/${sharedItemId}`, {
    quantity: Number(quantity) || 0,
    expected_revision: _meta.revision || undefined
  })
  applyPayload(res)
  return getMirror()
}

async function removeItem(sharedItemId) {
  return setQuantity(sharedItemId, 0)
}

async function clear() {
  await auth.ensureLogin()
  const params = tableParams()
  const res = await api.post('/miniprogram/table-party/cart/clear', {
    ...params,
    expected_revision: _meta.revision || undefined
  })
  applyPayload(res)
  return getMirror()
}

async function fetchTableOrders() {
  if (!shouldUseShared()) return { items: [], count: 0 }
  try {
    await auth.ensureLogin()
    return await api.get('/miniprogram/table-party/orders', tableParams())
  } catch (_) {
    return { items: [], count: 0 }
  }
}

function startPolling(intervalMs) {
  stopPolling()
  if (!shouldUseShared()) return
  const ms = Math.max(2000, Number(intervalMs) || 2800)
  _pollTimer = setInterval(() => {
    if (!shouldUseShared()) return
    pull().catch(() => {})
  }, ms)
}

function stopPolling() {
  if (_pollTimer) {
    clearInterval(_pollTimer)
    _pollTimer = null
  }
}

module.exports = {
  shouldUseShared,
  isActive,
  getMirror,
  getMeta,
  subscribe,
  joinAndPull,
  pull,
  addItem,
  setQuantity,
  removeItem,
  clear,
  fetchTableOrders,
  startPolling,
  stopPolling
}
