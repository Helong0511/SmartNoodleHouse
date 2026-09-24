const { redirectToPackage } = require('../../utils/page-redirect')

Page({
  onLoad(query) {
    redirectToPackage('/packageOrder/result/result', query)
  }
})
