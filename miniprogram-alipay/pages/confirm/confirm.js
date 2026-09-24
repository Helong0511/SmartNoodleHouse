const { redirectToPackage } = require('../../utils/page-redirect')

Page({
  onLoad(query) {
    redirectToPackage('/packageOrder/confirm/confirm', query)
  }
})
