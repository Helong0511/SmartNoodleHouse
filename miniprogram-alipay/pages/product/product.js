const api = require('../../utils/api')
const auth = require('../../utils/auth')

Page({
  data: { product: null, selected: {}, multi: {} },
  async onLoad(query) {
    try {
      const product = await api.get(`/products/${query.id}`)
      const selected = {}
      ;(product.option_groups || []).forEach(g => {
        if (g.selection_type === 'SINGLE' && g.options && g.options.length) {
          selected[g.id] = (g.options.find(o => o.code === 'NORMAL') || g.options[0]).id
        }
      })
      this.setData({ product, selected })
    } catch (e) {
      my.showToast({ content: e.message || '加载失败', type: 'fail' })
    }
  },
  selectSingle(e) {
    const { gid, oid } = e.target.dataset
    this.setData({ [`selected.${gid}`]: oid })
  },
  selectMulti(e) {
    const { gid, oid } = e.target.dataset
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
      if (g.selection_type === 'SINGLE' && this.data.selected[g.id]) optionIds.push(this.data.selected[g.id])
      if (g.selection_type === 'MULTIPLE') optionIds.push(...(this.data.multi[g.id] || []))
    })
    const cart = auth.getCart()
    cart.push({ product_id: p.id, name: p.name, quantity: 1, option_ids: optionIds, subtotal: p.price })
    auth.saveCart(cart)
    my.showToast({ content: '已加入' })
    setTimeout(() => my.navigateBack(), 400)
  }
})
