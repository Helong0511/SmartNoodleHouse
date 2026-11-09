const api = require('../../utils/api')
const auth = require('../../utils/auth')
const dineCtx = require('../../utils/context')

const STATUS_LABEL = {
  CLAIMED: '待核销',
  USED: '已核销',
  EXPIRED: '已失效',
  CANCELLED: '已取消'
}

const FLASH_SALE_REQUESTS_KEY = 'flash_sale_request_ids_v1'
const FLASH_SALE_REQUEST_TTL = 24 * 60 * 60 * 1000

function requestScope(campaignId, storeId) {
  const customerId = Number(auth.readStorage('customer_id') || 0)
  return `${customerId}:${Number(storeId)}:${Number(campaignId)}`
}

function readRequestMap() {
  try {
    const value = wx.getStorageSync(FLASH_SALE_REQUESTS_KEY)
    return value && typeof value === 'object' && !Array.isArray(value) ? value : {}
  } catch (_) {
    return {}
  }
}

function getRequestId(campaignId, storeId) {
  const now = Date.now()
  const scope = requestScope(campaignId, storeId)
  const entries = Object.entries(readRequestMap())
    .filter(([, item]) => item && item.id && now - Number(item.createdAt || 0) < FLASH_SALE_REQUEST_TTL)
    .sort((a, b) => Number(b[1].createdAt || 0) - Number(a[1].createdAt || 0))
    .slice(0, 49)
  const cache = Object.fromEntries(entries)
  if (cache[scope]) return cache[scope].id
  const id = `fs${now.toString(36)}${Math.random().toString(36).slice(2, 10)}`
  cache[scope] = { id, createdAt: now }
  try {
    wx.setStorageSync(FLASH_SALE_REQUESTS_KEY, cache)
  } catch (_) {
    throw new Error('无法保存抢购幂等凭证，已阻止重复提交风险')
  }
  return id
}

function clearRequestId(campaignId, storeId) {
  const cache = readRequestMap()
  delete cache[requestScope(campaignId, storeId)]
  try { wx.setStorageSync(FLASH_SALE_REQUESTS_KEY, cache) } catch (_) {}
}

/** 列表按钮状态：每人限购 / 已持有一份 / 售罄 都在前端做展示，服务端仍会二次校验 */
function decorateActive(items) {
  return (items || []).map((it) => {
    const held = !!it.my_claim
    const limit = Math.max(1, Number(it.per_customer_limit) || 1)
    const limitReached = Number(it.my_claimed_count || 0) >= limit
    let btnText = '免费抢'
    if (held) btnText = '待核销'
    else if (Number(it.remaining_quantity) <= 0) btnText = '已抢完'
    else if (limitReached) btnText = '已达上限'
    return {
      ...it,
      btnText,
      disabled: held || limitReached || Number(it.remaining_quantity) <= 0,
      stockText: `剩余 ${Number(it.remaining_quantity) || 0}/${Number(it.total_quantity) || 0}`
    }
  })
}

function decorateMine(items) {
  return (items || []).map((it) => {
    const status = String(it.status || '').toUpperCase()
    return {
      ...it,
      status,
      statusLabel: STATUS_LABEL[status] || status || '—'
    }
  })
}

Page({
  data: {
    storeId: null,
    storeName: '',
    tab: 'active',
    items: [],
    mine: [],
    loading: false,
    claimingId: null
  },

  onLoad() {
    this.applyCtx(dineCtx.currentDisplay())
  },

  onShow() {
    dineCtx.ensureDineContext()
      .then((ctx) => {
        this.applyCtx(ctx)
        return this.reload()
      })
      .catch(() => this.reload())
  },

  onTableChange(e) {
    if (e && e.detail) {
      this.applyCtx(e.detail)
      this.reload()
    }
  },

  applyCtx(ctx) {
    const c = ctx || {}
    this.setData({
      storeId: c.storeId || null,
      storeName: c.storeName || ''
    })
  },

  switchTab(e) {
    const tab = String((e.currentTarget.dataset && e.currentTarget.dataset.tab) || 'active')
    if (tab === this.data.tab) return
    this.setData({ tab })
    if (tab === 'mine') this.loadMine()
  },

  reload() {
    const jobs = [this.loadActive()]
    if (this.data.tab === 'mine') jobs.push(this.loadMine())
    return Promise.all(jobs)
  },

  async loadActive() {
    const storeId = this.data.storeId
    if (!storeId) {
      this.setData({ items: [] })
      return
    }
    try {
      const res = await api.get('/miniprogram/flash-sales', { store_id: storeId })
      this.setData({ items: decorateActive((res && res.items) || []) })
    } catch (e) {
      this.setData({ items: [] })
      wx.showToast({ title: (e && e.message) || '加载失败', icon: 'none' })
    }
  },

  async loadMine() {
    try {
      await auth.ensureLogin()
    } catch (_) {
      this.setData({ mine: [] })
      return
    }
    const storeId = this.data.storeId
    try {
      const res = await api.get(
        '/miniprogram/flash-sales/mine',
        storeId ? { store_id: storeId } : {}
      )
      this.setData({ mine: decorateMine((res && res.items) || []) })
    } catch (_) {
      this.setData({ mine: [] })
    }
  },

  async onClaim(e) {
    const campaignId = Number(e.currentTarget.dataset.id)
    if (!campaignId || this.data.claimingId) return
    if (!this.data.storeId) {
      wx.showToast({ title: '请先扫码或选择门店', icon: 'none' })
      return
    }
    try {
      await auth.ensureLogin()
    } catch (_) {
      wx.showToast({ title: '请先登录', icon: 'none' })
      return
    }
    this.setData({ claimingId: campaignId })
    try {
      const res = await api.post(`/miniprogram/flash-sales/${campaignId}/claim`, {
        store_id: this.data.storeId,
        client_request_id: getRequestId(campaignId, this.data.storeId)
      })
      clearRequestId(campaignId, this.data.storeId)
      if (res && res.already_claimed) {
        wx.showToast({ title: '已抢到，请勿重复抢购', icon: 'none' })
      } else {
        const deadline = (res && (res.expires_at || res.verify_deadline_at)) || ''
        wx.showModal({
          title: '抢购成功',
          content: `核销码 ${res.claim_no}${deadline ? `，请在 ${deadline} 前到店核销` : ''}`,
          showCancel: false
        })
      }
      this.setData({ tab: 'mine' })
      await this.reload()
    } catch (err) {
      wx.showToast({ title: (err && err.message) || '抢购失败', icon: 'none' })
    } finally {
      this.setData({ claimingId: null })
    }
  },

  onPullDownRefresh() {
    this.reload().finally(() => wx.stopPullDownRefresh())
  }
})
