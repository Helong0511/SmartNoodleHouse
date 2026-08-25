const api = require('../../utils/api')
const auth = require('../../utils/auth')

Page({
  data: {
    product: null,
    selected: {},
    multi: {}
  },

  async onLoad(query) {
    try {
      const product = await api.get(`/products/${query.id}`)
      const selected = {}
      ;(product.option_groups || []).forEach(g => {
        if (g.selection_type === 'SINGLE' && g.options && g.options.length) {
          const normal = g.options.find(o => o.code === 'NORMAL') || g.options[0]
          selected[g.id] = normal.id
        }
      })
      this.setData({ product, selected })
    } catch (e) {
      wx.showToast({ title: e.message || '加载失败', icon: 'none' })
    }
  },

  selectSingle(e) {
    const { gid, oid } = e.currentTarget.dataset
    this.setData({ [`selected.${gid}`]: oid })
  },

  selectMulti(e) {
    const { gid, oid } = e.currentTarget.dataset
    const list = [...(this.data.multi[gid] || [])]
    const idx = list.indexOf(oid)
    if (idx >= 0) list.splice(idx, 1)
    else list.push(oid)
    this.setData({ [`multi.${gid}`]: list })
  },

  addCart() {
    const p = this.data.product
    const optionIds = []
    ;(p.option_groups || []).forEach(g => {
      if (g.selection_type === 'SINGLE' && this.data.selected[g.id]) {
        optionIds.push(this.data.selected[g.id])
      }
      if (g.selection_type === 'MULTIPLE') {
        optionIds.push(...(this.data.multi[g.id] || []))
      }
    })
    const cart = auth.getCart()
    cart.push({
      product_id: p.id,
      name: p.name,
      quantity: 1,
      option_ids: optionIds,
      subtotal: p.price
    })
    auth.saveCart(cart)
    wx.showToast({ title: '已加入', icon: 'success' })
    setTimeout(() => wx.navigateBack(), 500)
  }
})
