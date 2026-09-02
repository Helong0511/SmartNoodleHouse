const dineCtx = require('../../utils/context')

Component({
  props: {
    storeName: '',
    storeNo: '',
    storeAddress: '',
    storePhone: '',
    tableName: '',
    tableNo: '',
    compact: false,
    changeable: true,
    onTableChange: null
  },

  methods: {
    onTapTable() {
      if (!this.props.changeable || !this.props.tableName) return
      my.confirm({
        title: '更换桌号',
        content: '是否更改桌号？',
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
