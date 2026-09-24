const dineCtx = require('../../utils/context')
const media = require('../../utils/media')

let _logoCache = { raw: '', src: '/assets/logo.png' }

function resolveLogoCached(raw) {
  const s = String(raw || '').trim()
  if (s === _logoCache.raw && _logoCache.src) return _logoCache.src
  const src = s ? (media.resolveUrl(s) || '/assets/logo.png') : '/assets/logo.png'
  _logoCache = { raw: s, src }
  return src
}

function pickField(propVal, ctxVal) {
  const p = propVal == null ? '' : String(propVal)
  if (p !== '') return propVal
  return ctxVal
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
    seatLabel: '',
    compact: false,
    changeable: true,
    autoSync: true,
    onTableChange: null
  },

  data: {
    logoSrc: '/assets/logo.png',
    viewStoreName: '',
    viewStoreNo: '',
    viewStoreAddress: '',
    viewStorePhone: '',
    viewTableName: '',
    viewTableNo: '',
    viewSeatLabel: '桌号'
  },

  didMount() {
    if (this.props.autoSync !== false) {
      this._unsubDine = dineCtx.subscribeDineContext((ctx) => {
        this.syncFromContext(ctx)
      })
    }
    this.syncFromContext()
  },

  didUnmount() {
    if (typeof this._unsubDine === 'function') {
      this._unsubDine()
      this._unsubDine = null
    }
  },

  didUpdate(prevProps) {
    // 仅 props 变化时同步，避免自身 setData 反复进入 didUpdate
    const p = this.props
    const prev = prevProps || {}
    if (
      prev.storeName === p.storeName
      && prev.storeNo === p.storeNo
      && prev.storeAddress === p.storeAddress
      && prev.storePhone === p.storePhone
      && prev.storeLogo === p.storeLogo
      && prev.tableName === p.tableName
      && prev.tableNo === p.tableNo
      && prev.seatLabel === p.seatLabel
      && prev.compact === p.compact
      && prev.changeable === p.changeable
      && prev.autoSync === p.autoSync
    ) {
      return
    }
    this.syncFromContext()
  },

  methods: {
    syncFromContext(ctx) {
      const useCtx = this.props.autoSync !== false
      const fromCtx = useCtx
        ? dineCtx.pageDineFields(ctx || dineCtx.currentDisplay())
        : {
          storeName: '',
          storeNo: '',
          storeAddress: '',
          storePhone: '',
          storeLogo: '',
          tableName: '',
          tableNo: '',
          seatLabel: '桌号'
        }
      const p = this.props
      const storeName = pickField(p.storeName, fromCtx.storeName)
      const storeNo = pickField(p.storeNo, fromCtx.storeNo)
      const storeAddress = pickField(p.storeAddress, fromCtx.storeAddress)
      const storePhone = pickField(p.storePhone, fromCtx.storePhone)
      const storeLogo = pickField(p.storeLogo, fromCtx.storeLogo)
      const tableName = pickField(p.tableName, fromCtx.tableName)
      const tableNo = pickField(p.tableNo, fromCtx.tableNo)
      const seatLabel = pickField(p.seatLabel, fromCtx.seatLabel) || '桌号'
      const logoSrc = resolveLogoCached(storeLogo)
      const patch = {
        viewStoreName: storeName || '',
        viewStoreNo: storeNo || '',
        viewStoreAddress: storeAddress || '',
        viewStorePhone: storePhone || '',
        viewTableName: tableName || '',
        viewTableNo: tableNo || '',
        viewSeatLabel: seatLabel,
        logoSrc
      }
      const d = this.data
      if (
        d.viewStoreName === patch.viewStoreName
        && d.viewStoreNo === patch.viewStoreNo
        && d.viewStoreAddress === patch.viewStoreAddress
        && d.viewStorePhone === patch.viewStorePhone
        && d.viewTableName === patch.viewTableName
        && d.viewTableNo === patch.viewTableNo
        && d.viewSeatLabel === patch.viewSeatLabel
        && d.logoSrc === patch.logoSrc
      ) {
        return
      }
      this.setData(patch)
    },

    onTapTable() {
      if (!this.props.changeable || !this.data.viewTableName) return
      const takeaway = this.data.viewSeatLabel === '用餐'
      my.confirm({
        title: takeaway ? '更换用餐方式' : '更换桌号',
        content: takeaway ? '是否重新扫码绑定堂食桌台？' : '是否更改桌号？',
        confirmButtonText: '是',
        cancelButtonText: '否',
        success: async (res) => {
          if (!res.confirm) return
          const ctx = await dineCtx.scanAndBindTable()
          if (ctx) {
            this.syncFromContext(ctx)
            if (typeof this.props.onTableChange === 'function') {
              this.props.onTableChange(ctx)
            }
          }
        }
      })
    }
  }
})
