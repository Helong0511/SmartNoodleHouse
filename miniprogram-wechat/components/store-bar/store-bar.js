const dineCtx = require('../../utils/context')

Component({
  properties: {
    storeName: { type: String, value: '' },
    storeNo: { type: String, value: '' },
    storeAddress: { type: String, value: '' },
    storePhone: { type: String, value: '' },
    tableName: { type: String, value: '' },
    tableNo: { type: String, value: '' },
    seatLabel: { type: String, value: '桌号' },
    compact: { type: Boolean, value: false },
    /** 是否允许点击桌号换桌（订单结果页等可关闭） */
    changeable: { type: Boolean, value: true }
  },

  methods: {
    onTapTable() {
      if (!this.properties.changeable || !this.properties.tableName) return
      const takeaway = this.properties.seatLabel === '用餐'
      wx.showModal({
        title: takeaway ? '更换用餐方式' : '更换桌号',
        content: takeaway ? '是否重新扫码绑定堂食桌台？' : '是否更改桌号？',
        cancelText: '否',
        confirmText: '是',
        success: async (res) => {
          if (!res.confirm) return
          const ctx = await dineCtx.scanAndBindTable()
          if (ctx) {
            this.triggerEvent('tablechange', ctx)
          }
        }
      })
    }
  }
})
