const api = require('./api')
const auth = require('./auth')

function getAppSafe() {
  try {
    return getApp()
  } catch (_) {
    return null
  }
}

/** 与后端 DiningSession 默认 4 小时对齐；过期后不得静默复用昨日桌台 */
const DINE_BOUND_TTL_MS = 4 * 60 * 60 * 1000
const DINE_BOUND_AT_KEY = 'dine_bound_at'

function nowMs() {
  return Date.now()
}

function readBoundAt() {
  try {
    const v = auth.readStorage(DINE_BOUND_AT_KEY)
    const n = Number(v)
    return Number.isFinite(n) && n > 0 ? n : 0
  } catch (_) {
    return 0
  }
}

function writeBoundAt(ts) {
  try {
    my.setStorageSync({ key: DINE_BOUND_AT_KEY, data: Number(ts) || nowMs() })
  } catch (_) {}
}

function isDineCacheFresh(boundAt) {
  const at = Number(boundAt) || 0
  if (!at) return false
  return nowMs() - at < DINE_BOUND_TTL_MS
}

function clearDineContext() {
  const g = getAppSafe()
  if (g && g.globalData) {
    g.globalData.table = null
    g.globalData.store = null
    g.globalData.tableToken = ''
  }
  try {
    my.removeStorageSync({ key: 'dine_table' })
    my.removeStorageSync({ key: 'dine_store' })
    my.removeStorageSync({ key: 'table_token' })
    my.removeStorageSync({ key: DINE_BOUND_AT_KEY })
  } catch (_) {}
  try {
    notifyDineContextChange(displayPayload(null, null))
  } catch (_) {}
}

/** 外带/外卖虚拟桌（种子 table_no=WM），不是堂食实体桌 */
function isTakeawayChannelTable(table) {
  if (!table) return false
  const no = String(table.table_no || '').trim().toUpperCase()
  if (no === 'WM' || no === 'TKW' || no === 'TAKEAWAY') return true
  const name = String(table.table_name || '')
  return /外卖|外带自取/.test(name)
}

function tableLabelRaw(table) {
  if (!table) return ''
  return table.table_name || table.table_no || ''
}

function tableLabel(table) {
  if (!table) return ''
  if (isTakeawayChannelTable(table)) return '外带自取'
  return tableLabelRaw(table)
}

function storeLabel(store) {
  if (!store) return ''
  return store.name || ''
}

/** 桌台/门店变更订阅：扫码绑桌后立即刷新各页 store-bar */
const _dineListeners = []

function subscribeDineContext(fn) {
  if (typeof fn !== 'function') return function () {}
  _dineListeners.push(fn)
  return function unsubscribe() {
    const i = _dineListeners.indexOf(fn)
    if (i >= 0) _dineListeners.splice(i, 1)
  }
}

function notifyDineContextChange(ctx) {
  const payload = ctx || currentDisplay()
  for (let i = 0; i < _dineListeners.length; i++) {
    try {
      _dineListeners[i](payload)
    } catch (_) {}
  }
}

async function enrichStore(store, table) {
  let s = store || null
  const sid = (table && table.store_id) || (s && (s.id || s.store_id)) || null
  if (sid && (!s || !s.name)) {
    try {
      s = await api.get(`/miniprogram/stores/${sid}`, {}, false)
    } catch (_) {}
  }
  return s
}

function applyContext(table, store, options) {
  const g = getAppSafe()
  if (!g) return
  const opts = options || {}
  if (table) {
    const storedToken = g.globalData.tableToken || auth.readStorage('table_token') || ''
    if (!table.qr_token && storedToken) {
      table = { ...table, qr_token: storedToken }
    }
    g.globalData.table = table
    my.setStorageSync({ key: 'dine_table', data: table })
    if (table.qr_token) {
      g.globalData.tableToken = table.qr_token
      my.setStorageSync({ key: 'table_token', data: table.qr_token })
    }
    if (opts.boundAt != null) {
      writeBoundAt(opts.boundAt)
    } else if (opts.expiresAt) {
      const exp = Date.parse(opts.expiresAt)
      if (Number.isFinite(exp)) {
        writeBoundAt(Math.max(nowMs() - (DINE_BOUND_TTL_MS - Math.max(0, exp - nowMs())), nowMs() - DINE_BOUND_TTL_MS + 1000))
      } else {
        writeBoundAt(nowMs())
      }
    } else {
      writeBoundAt(nowMs())
    }
  }
  if (store) {
    const prevStoreId = (g.globalData.store && (g.globalData.store.id || g.globalData.store.store_id)) || null
    const newStoreId = store.id || store.store_id || null
    g.globalData.store = store
    my.setStorageSync({ key: 'dine_store', data: store })
    // P2-1：门店切换（或首次绑定）时，校验购物车归属，跨店一律清空
    if (newStoreId && Number(newStoreId) !== Number(prevStoreId)) {
      try {
        if (typeof auth.ensureCartForCurrentStore === 'function') {
          auth.ensureCartForCurrentStore()
        }
      } catch (_) {}
      try {
        // 门店切换时只加载一次配置模块，避免重复 require 分散失效与重载逻辑。
        const clientConfig = require('./clientConfig')
        clientConfig.invalidateClientConfig()
        clientConfig.loadClientConfig(true, newStoreId).catch(() => {})
      } catch (_) {}
    }
  }
  if (!opts.silent) {
    notifyDineContextChange(displayPayload(
      store || (g && g.globalData && g.globalData.store) || null,
      table || (g && g.globalData && g.globalData.table) || null
    ))
  }
}

function readCachedContext() {
  const g = getAppSafe()
  if (!g) return { table: null, store: null, token: '', fresh: false }
  const boundAt = readBoundAt()
  const fresh = isDineCacheFresh(boundAt)
  // fresh 仅表示本地绑定时间戳是否在 4h 内，不再作为删除静态 qr_token 的依据。
  // 凭证是否有效统一由后端 /entry 裁决，避免真机因 boundAt 写入竞态/时间异常
  // 就在 onShow 读缓存时删掉刚扫码成功的 token，导致反复要求「扫码绑桌」。
  let table = g.globalData.table || auth.readStorage('dine_table') || null
  const store = g.globalData.store || auth.readStorage('dine_store') || null
  const token = g.globalData.tableToken || auth.readStorage('table_token') || ''
  if (table && !g.globalData.table) g.globalData.table = table
  if (store && !g.globalData.store) g.globalData.store = store
  return { table, store, token, fresh }
}

function contextReady(table, store, token) {
  if (!token) return true
  const sid = (table && table.store_id) || (store && store.id)
  if (!sid) return false
  if (!(store && store.name)) return false
  return !table || !!tableLabel(table)
}

function displayPayload(store, table) {
  const storeId = (store && store.id) || (table && table.store_id) || null
  const takeaway = isTakeawayChannelTable(table)
  const name = tableLabel(table)
  return {
    store,
    table,
    storeId,
    storeName: storeLabel(store),
    storeNo: (store && store.store_no) || '',
    storeAddress: (store && store.address) || '',
    storePhone: (store && store.phone) || '',
    storeLogo: (store && store.logo) || '',
    tableName: name,
    tableNo: takeaway ? '' : ((table && table.table_no) || ''),
    seatLabel: takeaway ? '用餐' : '桌号',
    isTakeawayChannel: takeaway,
    hasStore: !!(store && store.name),
    hasTable: !!name
  }
}

async function ensureDineContext(options = {}) {
  const force = !!(options && options.force)
  const g = getAppSafe()
  if (!g) return displayPayload(null, null)

  const cached = readCachedContext()
  let { table, store, token } = cached

  if (!force && contextReady(table, store, token)) {
    // 缓存命中也要写回 globalData，但 silent 避免每次 Tab onShow 广播抖动
    if (table || store) applyContext(table, store, { silent: true })
    return displayPayload(store, table)
  }

  if (token && (!table || !table.table_name)) {
    try {
      const entry = await api.get('/miniprogram/entry', { token, channel: 'alipay' }, false)
      if (entry && entry.table) {
        table = entry.table
        if (entry.store) store = entry.store
      }
    } catch (_) {}
  }

  if (table && table.store && !store) {
    store = table.store
  }

  const sid = (table && table.store_id) || (store && store.id)
  if (sid && (!store || !store.name)) {
    try {
      store = await api.get(`/miniprogram/stores/${sid}`, {}, false)
    } catch (_) {}
  }

  if (table || store) applyContext(table, store)
  return displayPayload(store, table)
}

function currentDisplay() {
  const { table, store } = readCachedContext()
  return displayPayload(store, table)
}

async function bindTableFromToken(token) {
  const raw = (token || '').trim()
  if (!raw) return null
  const entry = await api.get('/miniprogram/entry', { token: raw, channel: 'alipay' }, false)
  if (entry && entry.table) {
    const store = await enrichStore(entry.store || null, entry.table)
    applyContext(entry.table, store)
    return displayPayload(store, entry.table)
  }
  return null
}

function scoreScanCandidate(s) {
  const t = String(s || '')
  if (!t) return -1
  if (/^https?:\/\//i.test(t) || /^alipays?:\/\//i.test(t) || /^weixin:\/\//i.test(t)) return 100
  if (/token=/i.test(t)) return 90
  if (t.includes('://')) return 80
  if (t.length >= 20) return 60
  // 避免把接口状态码 "0"/"10"/"9000" 当成码内容
  if (/^\d{1,5}$/.test(t)) return 1
  return 20
}

function decodeScanRawData(b64) {
  if (!b64 || typeof b64 !== 'string') return ''
  try {
    const binary = typeof atob === 'function' ? atob(b64) : ''
    if (!binary) return ''
    // rawData 多为 UTF-8/ASCII 的码内容
    try {
      return decodeURIComponent(escape(binary))
    } catch (_) {
      return binary
    }
  } catch (_) {
    return ''
  }
}

/** 支付宝扫码字段不稳定：优先 result/qrCode，避免 code 实为状态码 */
function extractScanRaw(res) {
  if (res == null) return ''
  if (typeof res === 'string' || typeof res === 'number') return String(res).trim()
  if (typeof res !== 'object') return ''
  const list = [res.result, res.path, res.qrCode, res.code, res.barCode]
    .map((v) => (v == null ? '' : String(v).trim()))
    .filter(Boolean)
  list.sort((a, b) => scoreScanCandidate(b) - scoreScanCandidate(a))
  if (list.length && scoreScanCandidate(list[0]) >= 20) return list[0]
  const fromB64 = decodeScanRawData(res.rawData)
  if (fromB64) return fromB64.trim()
  return list[0] || ''
}

function showScanDebug(title, content) {
  try {
    const { getEnvDebugInfo } = require('../config/env')
    if (!(getEnvDebugInfo() && getEnvDebugInfo().isDevtools)) return
    my.alert({ title: title || '扫码调试', content: String(content || '').slice(0, 500) })
  } catch (_) {}
}

async function refreshTableBinding(options = {}) {
  const channel = (options && options.channel) || 'alipay'
  const allowReviveByTableId = !!(options && options.allowReviveByTableId)
  const cached = readCachedContext()
  const { table, store, fresh } = cached
  const token = String(cached.token || (table && table.qr_token) || '').trim()

  // 本地持有 token 即向后端校验（不受本地 4h TTL 限制）：
  // - 后端确认有效：续期本地绑定；明确无效：才清空；网络失败：保留本地兜底
  if (token) {
    try {
      const entry = await api.get('/miniprogram/entry', { token, channel }, false)
      if (entry && entry.table) {
        const fullStore = await enrichStore(entry.store || store || null, entry.table)
        applyContext(entry.table, fullStore)
        const ctx = displayPayload(fullStore, entry.table)
        return { ok: true, token: entry.table.qr_token || token, ctx }
      }
      clearDineContext()
      return { ok: false, token: '' }
    } catch (_) {
      return { ok: false, token }
    }
  }

  // 仅会话新鲜时才允许凭 table_id + qr_token 复活（P1 安全约束，与微信端对齐）
  const tableId = table && table.id
  const sid = (table && table.store_id) || (store && store.id)
  if (allowReviveByTableId && fresh && tableId && sid) {
    try {
      const bound = await api.post('/miniprogram/tables/bind', {
        table_id: tableId,
        store_id: sid,
        qr_token: token
      })
      if (bound && bound.qr_token) {
        const sess = bound.dining_session || null
        applyContext(bound, store, sess && sess.expires_at ? { expiresAt: sess.expires_at } : {})
        return { ok: true, token: bound.qr_token, ctx: displayPayload(store, bound) }
      }
    } catch (_) {}
  }
  return { ok: false, token: token || '' }
}

function isIdeFakeScanRaw(raw) {
  const s = String(raw || '').trim().toLowerCase()
  if (!s) return true
  // 支付宝开发者工具模拟器扫码常固定返回 fake_code，并非真实二维码内容
  return (
    s === 'fake_code'
    || s === 'fakecode'
    || s === 'scan_code'
    || s === 'scancode'
    || s === 'test'
    || s === 'mock'
  )
}

async function bindParsedScanRaw(raw, resolve) {
  const { parseTableTokenFromScan } = require('./scan')
  const { getEnvDebugInfo } = require('../config/env')
  const text = String(raw || '').trim()
  const devtools = !!(getEnvDebugInfo() && getEnvDebugInfo().isDevtools)

  if (devtools && isIdeFakeScanRaw(text)) {
    console.warn('[scan] IDE 模拟扫码返回 fake_code，改为粘贴一码通链接')
    my.showToast({ content: '模拟器扫码无效，请粘贴桌台链接', type: 'none', duration: 2500 })
    promptPasteTableToken(resolve)
    return
  }

  const token = parseTableTokenFromScan(text)
  // 仅开发者工具打印诊断信息，且不输出扫码原文（可能含完整桌台凭证）
  if (devtools) {
    console.warn('[scan] token=', token ? `${String(token).slice(0, 4)}***` : '(未识别)')
  }
  if (!token) {
    const tip = text
      ? `未识别到桌台码\n原文: ${text.slice(0, 120)}`
      : '未识别到桌台码'
    my.showToast({ content: '未识别到桌台码', type: 'none' })
    if (devtools) {
      showScanDebug('未识别到桌台码', tip)
      promptPasteTableToken(resolve)
      return
    }
    resolve(null)
    return
  }
  my.showLoading({ content: '识别桌台' })
  try {
    const ctx = await bindTableFromToken(token)
    my.hideLoading()
    if (ctx && ctx.hasTable) {
      my.showToast({ content: '桌台已绑定', type: 'success' })
      resolve(ctx)
      return
    }
    my.showToast({ content: '无效桌台二维码', type: 'none' })
    const debugMsg = `token=${token}\n原文=${text.slice(0, 200)}\n请扫「一码通扫」链接码，不要扫微信小程序码`
    if (devtools) {
      showScanDebug('无效桌台二维码', `${debugMsg}\n\n模拟器请改用粘贴一码通链接`)
      promptPasteTableToken(resolve)
      return
    }
    resolve(null)
  } catch (e) {
    my.hideLoading()
    const { localizeNativeErrMsg } = require('./errMsg')
    const raw = (e && e.message) || '识别失败'
    const msg = localizeNativeErrMsg(raw) || raw
    my.showToast({ content: msg, type: 'none', duration: 3500 })
    if (devtools) {
      showScanDebug('绑桌失败', `${msg}\ntoken=${token}\n原文=${text.slice(0, 160)}`)
      promptPasteTableToken(resolve)
      return
    }
    resolve(null)
  }
}

function promptPasteTableToken(resolve) {
  const { DEV_LAN_HOST } = require('../config/env')
  const sample = `http://${DEV_LAN_HOST || '192.168.1.110'}:5173/scan?token=…`
  my.prompt({
    title: '粘贴桌台码（模拟器）',
    message: '支付宝模拟器扫码只会返回 fake_code。请粘贴后台「一码通扫」完整链接，或直接粘贴 token。',
    placeholder: sample,
    okButtonText: '绑定',
    cancelButtonText: '取消',
    success: (res) => {
      if (!res || !res.ok) {
        resolve(null)
        return
      }
      const input = String(res.inputValue || '').trim()
      if (!input || isIdeFakeScanRaw(input)) {
        my.showToast({ content: '请粘贴真实一码通链接', type: 'none' })
        resolve(null)
        return
      }
      bindParsedScanRaw(input, resolve)
    },
    fail: () => resolve(null)
  })
}

function scanAndBindTable() {
  const { getEnvDebugInfo } = require('../config/env')
  return new Promise((resolve) => {
    my.scan({
      type: 'qr',
      scanType: ['qrCode'],
      hideAlbum: false,
      success: (res) => {
        const raw = extractScanRaw(res)
        bindParsedScanRaw(raw, resolve)
      },
      fail: (err) => {
        const msg = (err && (err.errorMessage || err.errMsg || err.message)) || ''
        if (msg.indexOf('cancel') >= 0 || msg.indexOf('取消') >= 0 || String(err && err.error) === '10') {
          // 开发者工具扫码常不可用：允许粘贴一码通链接
          if (getEnvDebugInfo().isDevtools) {
            promptPasteTableToken(resolve)
            return
          }
          resolve(null)
          return
        }
        if (getEnvDebugInfo().isDevtools) {
          promptPasteTableToken(resolve)
          return
        }
        my.showToast({ content: '扫码失败', type: 'none' })
        resolve(null)
      }
    })
  })
}

function pageDineFields(ctx) {
  const c = ctx || displayPayload(null, null)
  return {
    storeId: c.storeId || null,
    storeName: c.storeName,
    storeNo: c.storeNo,
    storeAddress: c.storeAddress,
    storePhone: c.storePhone,
    storeLogo: c.storeLogo || '',
    tableName: c.tableName,
    tableNo: c.tableNo,
    seatLabel: c.seatLabel || '桌号',
    isTakeawayChannel: c.isTakeawayChannel,
    hasTable: c.hasTable
  }
}

function dineFieldsEqual(data, fields) {
  if (!data || !fields) return false
  return data.storeId === fields.storeId
    && data.storeName === fields.storeName
    && data.storeNo === fields.storeNo
    && data.storeAddress === fields.storeAddress
    && data.storePhone === fields.storePhone
    && data.storeLogo === fields.storeLogo
    && data.tableName === fields.tableName
    && data.tableNo === fields.tableNo
    && data.seatLabel === fields.seatLabel
    && !!data.isTakeawayChannel === !!fields.isTakeawayChannel
    && !!data.hasTable === !!fields.hasTable
}

function applyPageDineFields(page, ctx) {
  if (!page || typeof page.setData !== 'function') return false
  const fields = pageDineFields(ctx)
  if (dineFieldsEqual(page.data, fields)) return false
  page.setData(fields)
  return true
}

module.exports = {
  DINE_BOUND_TTL_MS,
  ensureDineContext,
  currentDisplay,
  applyContext,
  clearDineContext,
  readCachedContext,
  displayPayload,
  pageDineFields,
  dineFieldsEqual,
  applyPageDineFields,
  isTakeawayChannelTable,
  bindTableFromToken,
  refreshTableBinding,
  scanAndBindTable,
  subscribeDineContext,
  notifyDineContextChange
}
