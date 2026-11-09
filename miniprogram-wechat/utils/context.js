const api = require('./api')

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
    const v = wx.getStorageSync(DINE_BOUND_AT_KEY)
    const n = Number(v)
    return Number.isFinite(n) && n > 0 ? n : 0
  } catch (_) {
    return 0
  }
}

function writeBoundAt(ts) {
  try {
    wx.setStorageSync(DINE_BOUND_AT_KEY, Number(ts) || nowMs())
  } catch (_) {}
}

function isDineCacheFresh(boundAt) {
  const at = Number(boundAt) || 0
  if (!at) return false
  return nowMs() - at < DINE_BOUND_TTL_MS
}

/** 清空本地桌台/门店缓存（过期或换桌失败时） */
function clearDineContext() {
  const g = getAppSafe()
  if (g && g.globalData) {
    g.globalData.table = null
    g.globalData.store = null
    g.globalData.tableToken = ''
  }
  try {
    wx.removeStorageSync('dine_table')
    wx.removeStorageSync('dine_store')
    wx.removeStorageSync('table_token')
    wx.removeStorageSync(DINE_BOUND_AT_KEY)
  } catch (_) {}
  notifyDineContextChange(displayPayload(null, null))
}

/**
 * P2-1：门店切换检测。传入将要生效的门店 id，若与 cart_store_id 不一致则清空购物车。
 * 在 applyContext 写入新 store 后调用。
 */
function reconcileCartWithStore(storeId) {
  try {
    const auth = require('./auth')
    if (auth && typeof auth.ensureCartForCurrentStore === 'function') {
      return auth.ensureCartForCurrentStore()
    }
  } catch (_) {}
  return false
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

/** entry 返回的 store 可能缺 name，补拉门店详情再展示 */
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

/** 将门店/桌台写入 globalData */
function applyContext(table, store, options) {
  const g = getAppSafe()
  if (!g) return
  const opts = options || {}
  if (table) {
    const storedToken = opts.token || g.globalData.tableToken || wx.getStorageSync('table_token') || ''
    if (!table.qr_token && storedToken) {
      table = { ...table, qr_token: storedToken }
    }
    g.globalData.table = table
    wx.setStorageSync('dine_table', table)
    if (table.qr_token) {
      g.globalData.tableToken = table.qr_token
      wx.setStorageSync('table_token', table.qr_token)
    }
    if (opts.boundAt != null) {
      writeBoundAt(opts.boundAt)
    } else if (opts.expiresAt) {
      const exp = Date.parse(opts.expiresAt)
      if (Number.isFinite(exp)) {
        // 以「还剩多久」反推绑定时间，保证与服务端 expires_at 对齐
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
    wx.setStorageSync('dine_store', store)
    // P2-1：门店切换（或首次绑定）时，校验购物车归属，跨店一律清空
    if (newStoreId && Number(newStoreId) !== Number(prevStoreId)) {
      reconcileCartWithStore(newStoreId)
      try {
        // 门店切换时只加载一次配置模块，避免重复 require 分散失效与重载逻辑。
        const clientConfig = require('./clientConfig')
        clientConfig.invalidateClientConfig()
        clientConfig.loadClientConfig(true, newStoreId).catch(() => {})
      } catch (_) {
        /* ignore */
      }
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
  // 注意：fresh 仅表示「本地绑定时间戳是否在 4h 内」，不再作为删除桌台数据的依据。
  // qr_token 是桌台静态长期凭证（后端 resolve_dining_table 不校验时间），会话有效期
  // 由后端 DiningSession.start_or_refresh 裁决。旧逻辑在 fresh=false 时直接
  // removeStorageSync('table_token')，真机上一旦 boundAt 写入竞态/时间异常/登录回写
  // 未补 boundAt，onShow 同步读缓存就会把刚扫码成功的 token 删掉，导致购物车/结算页
  // 反复要求「扫码绑桌」。凭证是否真的失效，统一由 refreshTableBinding 请求后端裁决。
  let table = g.globalData.table || wx.getStorageSync('dine_table') || null
  const store = g.globalData.store || wx.getStorageSync('dine_store') || null
  const token = g.globalData.tableToken || wx.getStorageSync('table_token') || ''
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

/** Tab 页 setData 用的门店/桌台字段 */
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

/** 页面 data 与桌台字段是否一致（避免无意义 setData） */
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

/** 差分写入页面桌台字段；有变更返回 true */
function applyPageDineFields(page, ctx) {
  if (!page || typeof page.setData !== 'function') return false
  const fields = pageDineFields(ctx)
  if (dineFieldsEqual(page.data, fields)) return false
  page.setData(fields)
  return true
}

/** 解析扫码/缓存的门店与桌台（各页 onShow 调用） */
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
      const entry = await api.get('/miniprogram/entry', { token, channel: 'wechat' }, false)
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
    // 虚拟外带桌不展示 WM 编号，避免被当成桌号
    tableNo: takeaway ? '' : ((table && table.table_no) || ''),
    seatLabel: takeaway ? '用餐' : '桌号',
    isTakeawayChannel: takeaway,
    hasStore: !!(store && store.name),
    hasTable: !!name
  }
}

/** 从 globalData 快速读取展示字段（不发起请求） */
function currentDisplay() {
  const { table, store } = readCachedContext()
  return displayPayload(store, table)
}

/** 根据 token 绑定桌台/门店，成功返回展示字段 */
async function bindTableFromToken(token) {
  const raw = (token || '').trim()
  if (!raw) return null
  const entry = await api.get('/miniprogram/entry', { token: raw, channel: 'wechat' }, false)
  if (entry && entry.table) {
    const store = await enrichStore(entry.store || null, entry.table)
    // /entry 不再回传永久 qr_token；首次扫码时使用本次请求凭证写入本地绑定。
    applyContext(entry.table, store, { token: raw })
    return displayPayload(store, entry.table)
  }
  return null
}

/**
 * 校验并刷新桌台 token。
 * 缓存过期后禁止仅凭 table_id 复活旧桌，须重新扫码。
 * @returns {{ ok: boolean, token?: string, ctx?: object }}
 */
async function refreshTableBinding(options = {}) {
  const channel = (options && options.channel) || 'wechat'
  const allowReviveByTableId = !!(options && options.allowReviveByTableId)
  const cached = readCachedContext()
  const { table, store, fresh } = cached
  const token = String(cached.token || (table && table.qr_token) || '').trim()

  // 只要本地持有 token 就向后端校验，不再受本地 4h TTL 限制：
  // - 后端确认有效：applyContext 续期本地时间戳，绑定继续可用（修复真机反复要求扫码）
  // - 后端明确返回无桌台（非网络错误）：凭证真的失效，才清除本地绑定
  // - 网络失败：保留本地静态 token 兜底，绝不清空（弱网/真机调试不再误删）
  if (token) {
    try {
      const entry = await api.get('/miniprogram/entry', { token, channel }, false)
      if (entry && entry.table) {
        const fullStore = await enrichStore(entry.store || store || null, entry.table)
        applyContext(entry.table, fullStore)
        const ctx = displayPayload(fullStore, entry.table)
        return { ok: true, token: entry.table.qr_token || token, ctx }
      }
      // 服务器可达但判定凭证无效（如桌台被删除/停用）：清除并要求重新扫码
      clearDineContext()
      return { ok: false, token: '' }
    } catch (_) {
      // 网络抖动：携带本地 token 返回失败，由调用方自行兜底
      return { ok: false, token }
    }
  }

  // 仅当会话仍新鲜时，才允许用 table_id 换 token（防昨日桌台污染）
  // P1 修复：bind 须传当前扫码 qr_token 凭证，后端校验同门店才允许绑定
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

/** 调起扫码并绑定桌台 */
function scoreScanCandidate(s) {
  const t = String(s || '')
  if (!t) return -1
  if (/^https?:\/\//i.test(t) || /^alipays?:\/\//i.test(t) || /^weixin:\/\//i.test(t)) return 100
  if (/token=/i.test(t)) return 90
  if (t.includes('://')) return 80
  if (t.length >= 20) return 60
  if (/^\d{1,5}$/.test(t)) return 1
  return 20
}

function decodeScanRawData(b64) {
  if (!b64 || typeof b64 !== 'string') return ''
  try {
    const binary = typeof atob === 'function' ? atob(b64) : ''
    if (!binary) return ''
    try {
      return decodeURIComponent(escape(binary))
    } catch (_) {
      return binary
    }
  } catch (_) {
    return ''
  }
}

/** 扫码字段优先取 URL/query；避免短数字状态码干扰 */
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
    wx.showModal({
      title: title || '扫码调试',
      content: String(content || '').slice(0, 500),
      showCancel: false
    })
  } catch (_) {}
}

function isIdeFakeScanRaw(raw) {
  const s = String(raw || '').trim().toLowerCase()
  if (!s) return true
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
    console.warn('[scan] IDE 模拟扫码无效，改为剪贴板/提示粘贴一码通链接')
    wx.showToast({ title: '模拟器扫码无效，请用剪贴板', icon: 'none', duration: 2500 })
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
    wx.showToast({ title: '未识别到桌台码', icon: 'none' })
    if (devtools) {
      showScanDebug('未识别到桌台码', tip)
      promptPasteTableToken(resolve)
      return
    }
    resolve(null)
    return
  }
  wx.showLoading({ title: '识别桌台' })
  try {
    const ctx = await bindTableFromToken(token)
    wx.hideLoading()
    if (ctx && ctx.hasTable) {
      wx.showToast({ title: '桌台已绑定', icon: 'success' })
      resolve(ctx)
      return
    }
    wx.showToast({ title: '无效桌台二维码', icon: 'none' })
    const debugMsg = `token=${token}\n原文=${text.slice(0, 200)}\n请扫「一码通扫」链接码，不要扫微信小程序码以外的无关码`
    if (devtools) {
      showScanDebug('无效桌台二维码', `${debugMsg}\n\n开发者工具可复制一码通链接到剪贴板后再试`)
      promptPasteTableToken(resolve)
      return
    }
    resolve(null)
  } catch (e) {
    wx.hideLoading()
    const { localizeNativeErrMsg } = require('./errMsg')
    const raw = (e && e.message) || '识别失败'
    const msg = localizeNativeErrMsg(raw) || raw
    wx.showToast({ title: msg, icon: 'none', duration: 3500 })
    if (devtools) {
      showScanDebug('绑桌失败', `${msg}\ntoken=${token}\n原文=${text.slice(0, 160)}`)
      promptPasteTableToken(resolve)
      return
    }
    resolve(null)
  }
}

/** 微信无 prompt 输入框：开发联调读剪贴板中的一码通链接 */
function promptPasteTableToken(resolve) {
  const { DEV_LAN_HOST } = require('../config/env')
  const sample = `http://${DEV_LAN_HOST || '192.168.1.110'}:5173/scan?token=…`
  wx.getClipboardData({
    success: (clip) => {
      const input = String((clip && clip.data) || '').trim()
      if (!input || isIdeFakeScanRaw(input)) {
        wx.showModal({
          title: '模拟器绑桌',
          content: `请先复制一码通完整链接到剪贴板（如 ${sample}），再点扫码；或用编译模式启动参数 token=&channel=wechat`,
          showCancel: false
        })
        resolve(null)
        return
      }
      wx.showModal({
        title: '使用剪贴板绑桌？',
        content: input.slice(0, 160),
        confirmText: '绑定',
        success: (r) => {
          if (r.confirm) bindParsedScanRaw(input, resolve)
          else resolve(null)
        },
        fail: () => resolve(null)
      })
    },
    fail: () => {
      wx.showToast({ title: '请先复制一码通链接', icon: 'none' })
      resolve(null)
    }
  })
}

function scanAndBindTable() {
  const { getEnvDebugInfo } = require('../config/env')
  return new Promise((resolve) => {
    wx.scanCode({
      onlyFromCamera: false,
      scanType: ['qrCode', 'barCode'],
      success: (res) => {
        const raw = extractScanRaw(res)
        bindParsedScanRaw(raw, resolve)
      },
      fail: (err) => {
        const msg = (err && (err.errMsg || err.message)) || ''
        if (msg.indexOf('cancel') >= 0 || msg.indexOf('取消') >= 0) {
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
        wx.showToast({ title: '扫码失败', icon: 'none' })
        resolve(null)
      }
    })
  })
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
  reconcileCartWithStore,
  subscribeDineContext,
  notifyDineContextChange
}
