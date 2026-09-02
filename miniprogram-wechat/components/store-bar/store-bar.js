const dineCtx = require('../../utils/context')

Component({
  properties: {
    storeName: { type: String, value: '' },
    storeNo: { type: String, value: '' },
    storeAddress: { type: String, value: '' },
    storePhone: { type: String, value: '' },
    tableName: { type: String, value: '' },
    tableNo: { type: String, value: '' },
    compact: { type: Boolean, value: false },
    /** 是否允许点击桌号换桌（订单结果页等可关闭） */
    changeable: { type: Boolean, value: true }
  },

  methods: {
    onTapTable() {
      if (!this.properties.changeable || !this.properties.tableName) return
      wx.showModal({
        title: '更换桌号',
        content: '是否更改桌号？',
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
