const dineCtx = require('../../utils/context')
const media = require('../../utils/media')

/** 三 Tab 共用同一份 logo 解析结果，避免切页重复 resolve */
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
  options: {
    // 去掉自定义组件宿主节点，避免微信端顶栏下方多出一层空白
    virtualHost: true
  },

  properties: {
    storeName: { type: String, value: '' },
    storeNo: { type: String, value: '' },
    storeAddress: { type: String, value: '' },
    storePhone: { type: String, value: '' },
    storeLogo: { type: String, value: '' },
    tableName: { type: String, value: '' },
    tableNo: { type: String, value: '' },
    seatLabel: { type: String, value: '' },
    compact: { type: Boolean, value: false },
    changeable: { type: Boolean, value: true },
    /** true：优先读全局桌台上下文（三 Tab）；false：纯受控 */
    autoSync: { type: Boolean, value: true }
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

  lifetimes: {
    attached() {
      if (this.properties.autoSync) {
        this._unsubDine = dineCtx.subscribeDineContext((ctx) => {
          this.syncFromContext(ctx)
        })
      }
      this.syncFromContext()
    },
    detached() {
      if (typeof this._unsubDine === 'function') {
        this._unsubDine()
        this._unsubDine = null
      }
    }
  },

  pageLifetimes: {
    show() {
      if (this.properties.autoSync) this.syncFromContext()
    }
  },

  observers: {
    'storeName, storeNo, storeAddress, storePhone, storeLogo, tableName, tableNo, seatLabel': function () {
      this.syncFromContext()
    }
  },

  methods: {
    syncFromContext(ctx) {
      const useCtx = this.properties.autoSync !== false
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
      const p = this.properties
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
      if (!this.properties.changeable || !this.data.viewTableName) return
      const takeaway = this.data.viewSeatLabel === '用餐'
      wx.showModal({
        title: takeaway ? '更换用餐方式' : '更换桌号',
        content: takeaway ? '是否重新扫码绑定堂食桌台？' : '是否更改桌号？',
        cancelText: '否',
        confirmText: '是',
        success: async (res) => {
          if (!res.confirm) return
          const ctx = await dineCtx.scanAndBindTable()
          if (ctx) {
            // applyContext 已广播；此处再同步一次保证当前页即时刷新
            this.syncFromContext(ctx)
            this.triggerEvent('tablechange', ctx)
          }
        }
      })
    }
  }
})
