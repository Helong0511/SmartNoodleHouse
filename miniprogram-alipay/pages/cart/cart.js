const auth = require('../../utils/auth')
Page({
  data: { cart: [] },
  onShow() { this.setData({ cart: auth.getCart() }) },
  goConfirm() { my.navigateTo({ url: '/pages/confirm/confirm' }) }
})
