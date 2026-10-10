const api = require('../../utils/api')
const auth = require('../../utils/auth')
const dineCtx = require('../../utils/context')
const clientConfig = require('../../utils/clientConfig')

const POLL_INTERVAL_MS = 15 * 1000

const STATUS_TEXT = {
  WAITING: '排队中',
  CALLED: '已叫号，请尽快入座',
  SEATED: '已入座',
  CANCELLED: '已取消',
  EXPIRED: '已过号'
}

Page({
  data: {
    loading: true,
    storeId: null,
    queueEnabled: true,
    peopleCount: 1,
    peopleOptions: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12],
    queue: null,
    statusText: '',
    taking: false,
    cancelling: false,
    loadFailed: false
  },

  onLoad() {
    this._pollTimer = null
    const ctx = dineCtx.currentDisplay()
    const sid = (ctx && ctx.storeId) || auth.currentStoreId() || null
    this.setData({ storeId: sid })
    clientConfig.loadClientConfig(false, sid).then(() => {
      this.setData({ queueEnabled: clientConfig.boolFlag('queue_enabled') })
    }).catch(() => {})
  },

  onShow() {
    if (this.data.queueEnabled) {
      this.loadCurrent()
    } else {
      this.setData({ loading: false })
    }
  },

  onHide() {
    this.stopPolling()
  },

  onUnload() {
    this.stopPolling()
  },

  stopPolling() {
    if (this._pollTimer) {
      clearInterval(this._pollTimer)
      this._pollTimer = null
    }
  },

  startPolling() {
    this.stopPolling()
    this._pollTimer = setInterval(() => {
      this.refreshCurrentQuietly()
    }, POLL_INTERVAL_MS)
  },

  resolveStoreId() {
    const ctx = dineCtx.currentDisplay()
    return (ctx && ctx.storeId) || this.data.storeId || auth.currentStoreId() || null
  },

  decorateQueue(q) {
    if (!q || typeof q !== 'object') return null
    const status = String(q.status || 'WAITING').toUpperCase()
    return {
      ...q,
      status,
      statusText: STATUS_TEXT[status] || status,
      isWaiting: status === 'WAITING',
      isCalled: status === 'CALLED',
      active: status === 'WAITING' || status === 'CALLED'
    }
  },

  async loadCurrent() {
    const sid = this.resolveStoreId()
    if (!sid) {
      this.setData({ loading: false, loadFailed: true })
      return
    }
    this.setData({ loading: true, loadFailed: false })
    try {
      await auth.ensureLogin()
      const data = await api.get('/miniprogram/queue/current', { store_id: sid })
      const queue = this.decorateQueue(data)
      this.setData({ queue, loading: false })
      if (queue && queue.active) this.startPolling()
      else this.stopPolling()
    } catch (e) {
      this.setData({ loading: false, loadFailed: true })
      wx.showToast({ title: (e && e.message) || '排队信息加载失败', icon: 'none' })
    }
  },

  /** 轮询用的静默刷新：失败不打扰 */
  async refreshCurrentQuietly() {
    const sid = this.resolveStoreId()
    if (!sid) return
    try {
      const data = await api.get('/miniprogram/queue/current', { store_id: sid })
      const queue = this.decorateQueue(data)
      this.setData({ queue })
      if (!queue || !queue.active) this.stopPolling()
    } catch (_) { /* 静默 */ }
  },

  onPickPeople(e) {
    const n = Number(e.currentTarget.dataset.n)
    if (!n || this.data.taking) return
    this.setData({ peopleCount: n })
  },

  async takeNumber() {
    if (this.data.taking) return
    const sid = this.resolveStoreId()
    if (!sid) {
      wx.showToast({ title: '请先扫桌码选择门店', icon: 'none' })
      return
    }
    this.setData({ taking: true })
    try {
      await auth.ensureLogin()
      await api.post('/miniprogram/queue/take', {
        store_id: sid,
        people_count: this.data.peopleCount
      })
      wx.showToast({ title: '取号成功', icon: 'success' })
      await this.loadCurrent()
    } catch (e) {
      wx.showToast({ title: (e && e.message) || '取号失败，请稍后重试', icon: 'none' })
    } finally {
      this.setData({ taking: false })
    }
  },

  cancelQueue() {
    const q = this.data.queue
    if (!q || !q.id || this.data.cancelling) return
    wx.showModal({
      title: '取消排队',
      content: '确定取消当前排队号吗？取消后需重新取号。',
      confirmText: '取消排队',
      confirmColor: '#e6433a',
      success: async (r) => {
        if (!r.confirm) return
        this.setData({ cancelling: true })
        try {
          await api.post(`/miniprogram/queue/${q.id}/cancel`, {})
          wx.showToast({ title: '已取消排队', icon: 'none' })
          this.stopPolling()
          this.setData({ queue: null })
        } catch (e) {
          wx.showToast({ title: (e && e.message) || '取消失败，请稍后重试', icon: 'none' })
        } finally {
          this.setData({ cancelling: false })
        }
      }
    })
  },

  goMenu() {
    wx.switchTab({ url: '/pages/menu/menu' })
  }
})
