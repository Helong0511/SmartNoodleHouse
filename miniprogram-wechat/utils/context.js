const api = require('./api')
const app = getApp()

function getAppSafe() {
  try {
    return getApp()
  } catch (_) {
    return null
  }
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

/** 将门店/桌台写入 globalData */
function applyContext(table, store) {
  const g = getAppSafe()
  if (!g) return
  if (table) {
    const storedToken = g.globalData.tableToken || wx.getStorageSync('table_token') || ''
    if (!table.qr_token && storedToken) {
      table = { ...table, qr_token: storedToken }
    }
    g.globalData.table = table
    wx.setStorageSync('dine_table', table)
    if (table.qr_token) {
      g.globalData.tableToken = table.qr_token
      wx.setStorageSync('table_token', table.qr_token)
    }
  }
  if (store) {
    g.globalData.store = store
    wx.setStorageSync('dine_store', store)
  }
}

function readCachedContext() {
  const g = getAppSafe()
  if (!g) return { table: null, store: null, token: '' }
  let table = g.globalData.table || wx.getStorageSync('dine_table') || null
  const store = g.globalData.store || wx.getStorageSync('dine_store') || null
  const token = g.globalData.tableToken || wx.getStorageSync('table_token') || ''
  if (table && !g.globalData.table) g.globalData.table = table
  if (store && !g.globalData.store) g.globalData.store = store
  return { table, store, token }
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
    tableName: c.tableName,
    tableNo: c.tableNo,
    seatLabel: c.seatLabel,
    isTakeawayChannel: c.isTakeawayChannel,
    hasTable: c.hasTable
  }
}

/** 解析扫码/缓存的门店与桌台（各页 onShow 调用） */
async function ensureDineContext(options = {}) {
  const force = !!(options && options.force)
  const g = getAppSafe()
  if (!g) return displayPayload(null, null)

  const cached = readCachedContext()
  let { table, store, token } = cached

  if (!force && contextReady(table, store, token)) {
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
    applyContext(entry.table, entry.store || null)
    return displayPayload(entry.store || null, entry.table)
  }
  return null
}

/**
 * 校验并刷新桌台 token（entry 失败时按 table_id 向服务端换 qr_token）
 * @returns {{ ok: boolean, token?: string, ctx?: object }}
 */
async function refreshTableBinding(options = {}) {
  const channel = (options && options.channel) || 'wechat'
  const cached = readCachedContext()
  const { table, store } = cached
  let token = String(cached.token || (table && table.qr_token) || '').trim()

  if (token) {
    try {
      const entry = await api.get('/miniprogram/entry', { token, channel }, false)
      if (entry && entry.table) {
        applyContext(entry.table, entry.store || store || null)
        const ctx = displayPayload(entry.store || store, entry.table)
        return { ok: true, token: entry.table.qr_token || token, ctx }
      }
    } catch (_) {}
  }

  const tableId = table && table.id
  const sid = (table && table.store_id) || (store && store.id)
  if (tableId && sid) {
    try {
      const bound = await api.post('/miniprogram/tables/bind', {
        table_id: tableId,
        store_id: sid
      })
      if (bound && bound.qr_token) {
        applyContext(bound, store)
        return { ok: true, token: bound.qr_token, ctx: displayPayload(store, bound) }
      }
    } catch (_) {}
  }
  return { ok: false, token }
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
  console.warn('[scan] raw=', text.slice(0, 180), 'token=', token)
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
    showScanDebug('无效桌台二维码', debugMsg)
    resolve(null)
  } catch (e) {
    wx.hideLoading()
    const msg = (e && e.message) || '识别失败'
    wx.showToast({ title: msg, icon: 'none', duration: 3500 })
    showScanDebug('绑桌失败', `${msg}\ntoken=${token}\n原文=${text.slice(0, 160)}`)
    if (devtools) {
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
  ensureDineContext,
  currentDisplay,
  applyContext,
  displayPayload,
  pageDineFields,
  isTakeawayChannelTable,
  bindTableFromToken,
  refreshTableBinding,
  scanAndBindTable
}
