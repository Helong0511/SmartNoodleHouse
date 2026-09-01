const api = require('./api')
const auth = require('./auth')

function getAppSafe() {
  try {
    return getApp()
  } catch (_) {
    return null
  }
}

function tableLabel(table) {
  if (!table) return ''
  return table.table_name || table.table_no || ''
}

function storeLabel(store) {
  if (!store) return ''
  return store.name || ''
}

function applyContext(table, store) {
  const g = getAppSafe()
  if (!g) return
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
  }
  if (store) {
    g.globalData.store = store
    my.setStorageSync({ key: 'dine_store', data: store })
  }
}

function readCachedContext() {
  const g = getAppSafe()
  if (!g) return { table: null, store: null, token: '' }
  let table = g.globalData.table || auth.readStorage('dine_table') || null
  const store = g.globalData.store || auth.readStorage('dine_store') || null
  const token = g.globalData.tableToken || auth.readStorage('table_token') || ''
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

function displayPayload(store, table) {
  const storeId = (store && store.id) || (table && table.store_id) || null
  return {
    store,
    table,
    storeId,
    storeName: storeLabel(store),
    storeNo: (store && store.store_no) || '',
    storeAddress: (store && store.address) || '',
    storePhone: (store && store.phone) || '',
    tableName: tableLabel(table),
    tableNo: (table && table.table_no) || '',
    hasStore: !!(store && store.name),
    hasTable: !!tableLabel(table)
  }
}

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
    applyContext(entry.table, entry.store || null)
    return displayPayload(entry.store || null, entry.table)
  }
  return null
}

async function refreshTableBinding(options = {}) {
  const channel = (options && options.channel) || 'alipay'
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

function scanAndBindTable() {
  const { parseTableTokenFromScan } = require('./scan')
  return new Promise((resolve) => {
    my.scan({
      scanType: ['qrCode'],
      hideAlbum: false,
      success: async (res) => {
        const token = parseTableTokenFromScan(res.result || res.code || '')
        if (!token) {
          my.showToast({ content: '未识别到桌台码', type: 'none' })
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
          } else {
            my.showToast({ content: '无效桌台二维码', type: 'none' })
            resolve(null)
          }
        } catch (e) {
          my.hideLoading()
          my.showToast({ content: (e && e.message) || '识别失败', type: 'none' })
          resolve(null)
        }
      },
      fail: (err) => {
        const msg = (err && (err.errorMessage || err.errMsg || err.message)) || ''
        if (msg.indexOf('cancel') >= 0 || msg.indexOf('取消') >= 0) {
          resolve(null)
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
    tableName: c.tableName,
    tableNo: c.tableNo,
    hasTable: c.hasTable
  }
}

module.exports = {
  ensureDineContext,
  currentDisplay,
  applyContext,
  displayPayload,
  pageDineFields,
  bindTableFromToken,
  refreshTableBinding,
  scanAndBindTable
}
