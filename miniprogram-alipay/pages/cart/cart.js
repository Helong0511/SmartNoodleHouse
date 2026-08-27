const auth = require('../../utils/auth')
const cartUtil = require('../../utils/cart')
const media = require('../../utils/media')

Page({
  data: {
    cart: [],
    total: '0.00',
    thumbStyle: ''
  },

  onShow() {
    const metrics = media.layoutMetrics()
    const px = Math.round(Math.max(56, (metrics.thumbPx || 80) * 0.85))
    this.setData({ thumbStyle: `width:${px}px;height:${px}px;` })
    this.refresh()
  },

  refresh() {
    const { list, total } = cartUtil.cartSummary(auth.getCart())
    const cart = list.map(item => ({
      ...item,
      imageUrl: item.imageUrl || media.resolveUrl(item.main_image || '')
    }))
    auth.saveCart(cart)
    this.setData({ cart, total })
  },

  changeQty(e) {
    const { index, delta } = e.currentTarget.dataset
    const cart = auth.getCart()
    const item = cart[index]
    if (!item) return
    const qty = (item.quantity || 1) + parseInt(delta, 10)
    if (qty <= 0) {
      cart.splice(index, 1)
    } else {
      cartUtil.normalizeItem(item)
      item.quantity = qty
      item.subtotal = (parseFloat(item.unit_price) * qty).toFixed(2)
    }
    auth.saveCart(cart)
    this.refresh()
  },

  removeItem(e) {
    const index = e.currentTarget.dataset.index
    const cart = auth.getCart()
    cart.splice(index, 1)
    auth.saveCart(cart)
    this.refresh()
  },

  goConfirm() {
    if (!this.data.cart.length) {
      my.showToast({ content: '购物车为空', type: 'fail' })
      return
    }
    my.navigateTo({ url: '/pages/confirm/confirm' })
  }
})
