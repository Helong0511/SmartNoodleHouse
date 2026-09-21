const api = require('../../utils/api')
const auth = require('../../utils/auth')
const tabCartBadge = require('../../utils/tabCartBadge')

const TABS = [
  { key: '', label: '全部' },
  { key: 'PAID', label: '已到账' },
  { key: 'PENDING', label: '待支付' },
  { key: 'CANCELLED,EXPIRED,FAILED', label: '取消/失败' }
]

function formatTime(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return String(iso).slice(0, 16).replace('T', ' ')
  const pad = (n) => (n < 10 ? `0${n}` : `${n}`)
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function statusClass(status) {
  const s = String(status || '').toUpperCase()
  if (s === 'PAID') return 'paid'
  if (s === 'PENDING') return 'pending'
  return ''
}

Page({
  data: {
    tabs: TABS,
    activeTab: '',
    records: [],
    page: 1,
    pageSize: 15,
    totalPages: 0,
    loading: false,
    loadingMore: false,
    noMore: false
  },

  onShow() {
    tabCartBadge.syncTabCartBadge()
    this.loadRecords(true, this.data.records.length > 0)
  },

  onPullDownRefresh() {
    this.loadRecords(true, false).finally(() => my.stopPullDownRefresh())
  },

  onReachBottom() {
    this.loadRecords(false)
  },

  onTabChange(e) {
    const key = (e.currentTarget.dataset && e.currentTarget.dataset.key) || ''
    if (key === this.data.activeTab) return
    this.setData({ activeTab: key })
    this.loadRecords(true, false)
  },

  async loadRecords(reset = true, silent = false) {
    if (reset) {
      if (this.data.loading) return
      if (!silent && !this.data.records.length) {
        this.setData({ loading: true, page: 1, noMore: false, records: [] })
      } else {
        this.setData({ page: 1, noMore: false })
      }
    } else {
      if (this.data.loading || this.data.loadingMore || this.data.noMore) return
      this.setData({ loadingMore: true })
    }
    const page = reset ? 1 : this.data.page + 1
    try {
      await auth.ensureLogin()
      const params = { page, page_size: this.data.pageSize }
      if (this.data.activeTab) params.status = this.data.activeTab
      const res = await api.get('/wallet/recharge/mine', params)
      const batch = ((res && res.items) || []).map((r) => ({
        ...r,
        credit_gift: Number(r.credit_gift || 0),
        timeText: formatTime(r.paid_at || r.created_at),
        statusClass: statusClass(r.status)
      }))
      const totalPages = (res && res.total_pages) || 0
      const records = reset ? batch : this.data.records.concat(batch)
      this.setData({
        records,
        page,
        totalPages,
        noMore: page >= totalPages || batch.length < this.data.pageSize
      })
    } catch (e) {
      if (reset) this.setData({ records: [] })
      if (e && e.message) my.showToast({ content: e.message, type: 'none' })
    } finally {
      this.setData({ loading: false, loadingMore: false })
    }
  },

  goOrders() {
    my.switchTab({ url: '/pages/orders/orders' })
  },

  goRecharge() {
    my.navigateTo({ url: '/pages/recharge/recharge' })
  }
})
