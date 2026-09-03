const dineCtx = require('../../utils/context')

Component({
  props: {
    storeName: '',
    storeNo: '',
    storeAddress: '',
    storePhone: '',
    tableName: '',
    tableNo: '',
    seatLabel: '桌号',
    compact: false,
    changeable: true,
    onTableChange: null
  },

  methods: {
    onTapTable() {
      if (!this.props.changeable || !this.props.tableName) return
      const takeaway = this.props.seatLabel === '用餐'
      my.confirm({
        title: takeaway ? '更换用餐方式' : '更换桌号',
        content: takeaway ? '是否重新扫码绑定堂食桌台？' : '是否更改桌号？',
        confirmButtonText: '是',
        cancelButtonText: '否',
        success: async (res) => {
          if (!res.confirm) return
          const ctx = await dineCtx.scanAndBindTable()
          if (ctx && typeof this.props.onTableChange === 'function') {
            this.props.onTableChange(ctx)
          }
        }
      })
    }
  }
})
