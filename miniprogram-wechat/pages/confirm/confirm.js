const api = require('../../utils/api')
const auth = require('../../utils/auth')
const cartUtil = require('../../utils/cart')
const media = require('../../utils/media')
const app = getApp()

Page({
  data: {
    remark: '',
    loading: false,
    tableName: '',
    tableToken: '',
    tableOptions: [],
    pickedLabel: '',
    needPack: false,
    cartPreview: [],
    subtotal: '0.00',
    packingFee: '0.00',
    payEstimate: '0.00',
    boxPriceMap: {}
  },

  onShow() {
    this.resolveTable()
    this.refreshSummary()
  },

  async refreshSummary() {
    const cart = auth.getCart()
    const { list, total } = cartUtil.cartSummary(cart)
    let boxMap = this.data.boxPriceMap || {}
    if (!Object.keys(boxMap).length) {
      try {
        const products = await api.get('/products', { page_size: 100 }, false)
        const items = (products && products.items) || []
        boxMap = {}
        items.forEach(p => {
          boxMap[p.id] = parseFloat(p.box_price || 0) || 0
        })
        this.setData({ boxPriceMap: boxMap })
      } catch (_) {}
    }
    let packing = 0
    const cartPreview = list.map(item => {
      const qty = item.quantity || 1
      const box = boxMap[item.product_id] || 0
      packing += box * qty
      return {
        ...item,
        imageUrl: item.imageUrl || media.resolveUrl(item.main_image || '')
      }
    })
    const sub = parseFloat(total) || 0
    const pack = Math.round(packing * 100) / 100
    const needPack = this.data.needPack
    const pay = needPack ? Math.round((sub + pack) * 100) / 100 : sub
    this.setData({
      cartPreview,
      subtotal: sub.toFixed(2),
      packingFee: pack.toFixed(2),
      payEstimate: pay.toFixed(2)
    })
  },

  async resolveTable() {
    let table = app.globalData.table
    let token = app.globalData.tableToken || wx.getStorageSync('table_token') || ''
    if (token && (!table || !table.table_name)) {
      try {
        const entry = await api.get('/miniprogram/entry', { token, channel: 'wechat' }, false)
        if (entry && entry.table) {
          table = entry.table
          app.globalData.table = table
          app.globalData.tableToken = table.qr_token
          wx.setStorageSync('table_token', table.qr_token)
        }
      } catch (_) {}
    }
    if (table && (table.table_name || table.table_no)) {
      this.setData({
        tableName: table.table_name || table.table_no,
        tableToken: table.qr_token || token
      })
      return
    }
    try {
      if (!app.globalData.customerToken) await auth.ensureLogin()
      const sid = (app.globalData.table && app.globalData.table.store_id) || null
      if (!sid) {
        // 多店场景禁止默认拉店 1；未扫码则提示先扫桌码
        this.setData({ tableOptions: [], tableName: '', tableToken: '', needScanHint: true })
        return
      }
      const list = await api.get('/miniprogram/tables', { store_id: sid })
      const options = (list || []).map(t => ({
        label: t.table_name || t.table_no,
        id: t.id,
        qr_token: t.qr_token || '',
        store_id: t.store_id,
        table_no: t.table_no,
        table_name: t.table_name
      }))
      this.setData({ tableOptions: options, tableName: '', tableToken: '', needScanHint: false })
    } catch (_) {
      this.setData({ tableOptions: [] })
    }
  },

  async onPickTable(e) {
    const idx = Number(e.detail.value)
    const t = this.data.tableOptions[idx]
    if (!t) return
    let token = t.qr_token
    if (!token && t.id) {
      try {
        const bound = await api.post('/miniprogram/tables/bind', {
          table_id: t.id,
          store_id: t.store_id || (app.globalData.table && app.globalData.table.store_id)
        })
        token = bound && bound.qr_token
        if (bound) {
          t.qr_token = token
          t.store_id = bound.store_id
        }
      } catch (err) {
        wx.showToast({ title: (err && err.message) || '选桌失败', icon: 'none' })
        return
      }
    }
    if (!token) {
      wx.showToast({ title: '无法获取桌台令牌', icon: 'none' })
      return
    }
    app.globalData.table = { ...t, qr_token: token }
    app.globalData.tableToken = token
    wx.setStorageSync('table_token', token)
    this.setData({
      tableName: t.label,
      tableToken: token,
      pickedLabel: t.label
    })
  },

  onPack(e) {
    const needPack = e.currentTarget.dataset.pack === '1' || e.currentTarget.dataset.pack === 1
    this.setData({ needPack }, () => this.refreshSummary())
  },

  onRemark(e) {
    this.setData({ remark: e.detail.value })
  },

  async submit() {
    const cart = auth.getCart()
    if (!cart.length) {
      wx.showToast({ title: '购物车为空', icon: 'none' })
      return
    }
    const tableToken = this.data.tableToken || app.globalData.tableToken || wx.getStorageSync('table_token')
    if (!tableToken) {
      wx.showToast({ title: '请先扫描或选择桌号', icon: 'none' })
      return
    }
    this.setData({ loading: true })
    try {
      if (!app.globalData.customerToken) await auth.ensureLogin()
      const order = await api.post('/orders', {
        store_id: app.globalData.table && app.globalData.table.store_id,
        table_token: tableToken,
        channel: 'WECHAT',
        need_pack: !!this.data.needPack,
        dining_type: this.data.needPack ? 'TAKEAWAY' : 'DINE_IN',
        remark: this.data.remark,
        idempotency_key: `wx_${app.globalData.customerId}_${Date.now()}`,
        items: cart.map(c => ({
          product_id: c.product_id,
          quantity: c.quantity || 1,
          option_ids: c.option_ids || []
        }))
      })
      await api.post(`/payments/orders/${order.id}/pay`, {
        provider: 'WECHAT',
        channel_type: 'miniprogram'
      })
      auth.saveCart([])
      wx.redirectTo({ url: `/pages/result/result?order_id=${order.id}` })
    } catch (e) {
      wx.showToast({ title: e.message || '下单失败', icon: 'none' })
    } finally {
      this.setData({ loading: false })
    }
  }
})
