const api = require('../../utils/api')
const auth = require('../../utils/auth')
const app = getApp()

function resolveUrl(path) {
  if (!path) return ''
  const s = String(path).trim()
  if (!s) return ''
  if (/^https?:\/\//i.test(s)) return s
  const base = (app.globalData && app.globalData.apiBase) || ''
  const origin = String(base).replace(/\/api\/v1\/?$/i, '')
  if (!origin) return s.startsWith('/') ? s : `/${s}`
  return s.startsWith('/') ? `${origin}${s}` : `${origin}/${s}`
}

Page({
  data: {
    nickname: '',
    customerNo: '',
    faceOn: false,
    prefs: [],
    orders: [],
    ordersLoading: false,
    reorderingId: null
  },

  async onShow() {
    try {
      if (!app.globalData.customerToken) await auth.ensureLogin()
      const face = await api.get('/miniprogram/customers/me/face-status')
      let prefs = []
      try {
        const cid = app.globalData.customerId
        const p = await api.get(`/customers/${cid}/preferences`)
        prefs = (p && p.preferences) || []
      } catch (_) {}
      this.setData({
        nickname: app.globalData.nickname || '',
        customerNo: app.globalData.customerNo || '',
        faceOn: !!(face && face.has_face),
        prefs
      })
      await this.loadOrders()
    } catch (e) {
      my.showToast({ content: e.message || '加载失败', type: 'fail' })
    }
  },

  async loadOrders() {
    this.setData({ ordersLoading: true })
    try {
      const res = await api.get('/orders/mine', { page: 1, page_size: 20 })
      const orders = ((res && res.items) || []).map((o) => ({
        ...o,
        timeText: this.formatTime(o.created_at)
      }))
      this.setData({ orders })
    } catch (_) {
      this.setData({ orders: [] })
    } finally {
      this.setData({ ordersLoading: false })
    }
  },

  formatTime(iso) {
    if (!iso) return ''
    const d = new Date(iso)
    if (Number.isNaN(d.getTime())) return String(iso).slice(0, 16).replace('T', ' ')
    const pad = (n) => (n < 10 ? `0${n}` : `${n}`)
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
  },

  onFaceSwitch(e) {
    if (e.detail.value) my.navigateTo({ url: '/pages/face/face' })
    else this.revokeFace()
  },

  async revokeFace() {
    try {
      await api.post('/miniprogram/face/revoke', {})
      app.globalData.faceStatus = 'REVOKED'
      this.setData({ faceOn: false })
      my.showToast({ content: '已关闭人脸迎宾' })
    } catch (e) {
      this.setData({ faceOn: true })
      my.showToast({ content: e.message || '操作失败', type: 'fail' })
    }
  },

  goMenu() {
    my.navigateTo({ url: '/pages/menu/menu' })
  },

  async reorderOrder(e) {
    const id = e.currentTarget.dataset.id
    if (!id || this.data.reorderingId) return
    this.setData({ reorderingId: id })
    try {
      my.showLoading({ content: '生成订单中' })
      const res = await api.post(`/orders/${id}/reorder`, {})
      const items = (res && res.cart_items) || []
      if (!items.length) throw new Error('没有可复购商品')
      const cart = items.map((it) => ({
        product_id: it.product_id,
        name: it.name,
        quantity: it.quantity || 1,
        option_ids: it.option_ids || [],
        option_labels: it.option_labels || [],
        option_text: it.option_text || (it.option_labels || []).join('、'),
        unit_price: it.unit_price,
        subtotal: it.subtotal,
        imageUrl: resolveUrl(it.imageUrl || it.main_image || ''),
        main_image: it.main_image || ''
      }))
      auth.saveCart(cart)
      my.hideLoading()
      const skipped = (res && res.skipped) || []
      my.alert({
        title: '已加入购物车',
        content: skipped.length
          ? `已加入 ${cart.length} 件；${skipped.length} 件不可用已跳过`
          : `已按历史订单加入 ${cart.length} 件`,
        success: () => my.navigateTo({ url: '/pages/cart/cart' })
      })
    } catch (err) {
      my.hideLoading()
      my.showToast({ content: err.message || '复购失败', type: 'fail' })
    } finally {
      this.setData({ reorderingId: null })
    }
  }
})
