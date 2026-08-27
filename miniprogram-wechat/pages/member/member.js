const api = require('../../utils/api')
const auth = require('../../utils/auth')
const media = require('../../utils/media')
const app = getApp()

Page({
  data: {
    nickname: '',
    displayName: '面馆会员',
    avatarLetter: '会',
    avatarUrl: '',
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
      const nickname = app.globalData.nickname || ''
      const displayName = nickname || '面馆会员'
      this.setData({
        nickname,
        displayName,
        avatarLetter: displayName.charAt(0) || '会',
        avatarUrl: app.globalData.avatarUrl || wx.getStorageSync('avatar_url') || '',
        customerNo: app.globalData.customerNo || '',
        faceOn: !!(face && face.has_face),
        prefs
      })
      await this.loadOrders()
    } catch (e) {
      wx.showToast({ title: e.message || '加载失败', icon: 'none' })
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
    } catch (e) {
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
    const on = e.detail.value
    if (on) {
      wx.navigateTo({ url: '/pages/face/face' })
    } else {
      this.revokeFace()
    }
  },

  async revokeFace() {
    try {
      await api.post('/miniprogram/face/revoke', {})
      app.globalData.faceStatus = 'REVOKED'
      this.setData({ faceOn: false })
      wx.showToast({ title: '已关闭人脸迎宾', icon: 'success' })
    } catch (e) {
      this.setData({ faceOn: true })
      wx.showToast({ title: e.message || '操作失败', icon: 'none' })
    }
  },

  goMenu() {
    wx.navigateTo({ url: '/pages/menu/menu' })
  },

  async reorderOrder(e) {
    const id = e.currentTarget.dataset.id
    if (!id || this.data.reorderingId) return
    this.setData({ reorderingId: id })
    try {
      wx.showLoading({ title: '生成订单中' })
      const res = await api.post(`/orders/${id}/reorder`, {})
      const items = (res && res.cart_items) || []
      if (!items.length) {
        throw new Error((res && res.message) || '没有可复购商品')
      }
      const cart = items.map((it) => ({
        product_id: it.product_id,
        name: it.name,
        quantity: it.quantity || 1,
        option_ids: it.option_ids || [],
        option_labels: it.option_labels || [],
        option_text: it.option_text || (it.option_labels || []).join('、'),
        unit_price: it.unit_price,
        subtotal: it.subtotal,
        imageUrl: media.resolveUrl(it.imageUrl || it.main_image || ''),
        main_image: it.main_image || ''
      }))
      auth.saveCart(cart)
      wx.hideLoading()
      const skipped = (res && res.skipped) || []
      if (skipped.length) {
        wx.showModal({
          title: '已加入购物车',
          content: `已加入 ${cart.length} 件；${skipped.length} 件不可用已跳过`,
          showCancel: false,
          success: () => wx.navigateTo({ url: '/pages/cart/cart' })
        })
      } else {
        wx.showToast({ title: '已按历史订单加入购物车', icon: 'success' })
        setTimeout(() => wx.navigateTo({ url: '/pages/cart/cart' }), 500)
      }
    } catch (err) {
      wx.hideLoading()
      wx.showToast({ title: err.message || '复购失败', icon: 'none' })
    } finally {
      this.setData({ reorderingId: null })
    }
  }
})
