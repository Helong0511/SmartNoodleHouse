const dineCtx = require('../../utils/context')
const media = require('../../utils/media')

function resolveLogo(raw) {
  const s = String(raw || '').trim()
  if (!s) return '/assets/logo.png'
  return media.resolveUrl(s) || '/assets/logo.png'
}

Component({
  props: {
    storeName: '',
    storeNo: '',
    storeAddress: '',
    storePhone: '',
    storeLogo: '',
    tableName: '',
    tableNo: '',
    seatLabel: '桌号',
    compact: false,
    changeable: true,
    onTableChange: null
  },

  data: {
    logoSrc: '/assets/logo.png'
  },

  didMount() {
    this.setData({ logoSrc: resolveLogo(this.props.storeLogo) })
  },

  didUpdate(prev) {
    if (prev.storeLogo !== this.props.storeLogo) {
      this.setData({ logoSrc: resolveLogo(this.props.storeLogo) })
    }
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
