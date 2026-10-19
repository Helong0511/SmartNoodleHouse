const api = require('../../utils/api')
const auth = require('../../utils/auth')
const cartUtil = require('../../utils/cart')
const media = require('../../utils/media')
const dineCtx = require('../../utils/context')
const prefUtil = require('../../utils/preferences')
const tracker = require('../../utils/tracker')
const productApi = require('../../utils/products')
const clientConfig = require('../../utils/clientConfig')
const navBar = require('../../utils/navBar')
const sharedCart = require('../../utils/sharedCart')
const app = getApp()

const PREFS_SKIP_LOGIN_MS = 60 * 1000

function normalizeList(data) {
  if (Array.isArray(data)) return data
  if (data && Array.isArray(data.items)) return data.items
  return []
}

/** 登录态指纹：宾客↔登录切换后须重拉菜单（生日菜因人而异）。 */
function menuAuthFinger() {
  try {
    return auth.getToken && auth.getToken() ? '1' : '0'
  } catch (_) {
    return '0'
  }
}

/** 首屏只放一部分菜，避免上百道菜一次 setData 撑满基础库数据上限。 */
function sliceMenuForFirstPaint(categories, maxProducts) {
  const all = categories || []
  let n = 0
  for (let i = 0; i < all.length; i++) {
    const count = ((all[i] && all[i].products) || []).length
    if (i > 0 && n >= maxProducts) return all.slice(0, i)
    if (count > maxProducts && i === 0) {
      return [Object.assign({}, all[0], { products: all[0].products.slice(0, maxProducts) })]
    }
    n += count
  }
  return all
}

/** hydrate 后路径差分；结构长度不一致时返回 null，调用方整表 setData。 */
function buildImageUrlPatch(prev, next) {
  if (!Array.isArray(prev) || !Array.isArray(next) || prev.length !== next.length) return null
  const patch = {}
  for (let ci = 0; ci < next.length; ci++) {
    const prevProds = (prev[ci] && prev[ci].products) || []
    const nextProds = (next[ci] && next[ci].products) || []
    if (prevProds.length !== nextProds.length) return null
    for (let pi = 0; pi < nextProds.length; pi++) {
      const oldUrl = prevProds[pi] && prevProds[pi].imageUrl
      const newUrl = nextProds[pi] && nextProds[pi].imageUrl
      if (oldUrl !== newUrl) {
        patch[`categories[${ci}].products[${pi}].imageUrl`] = newUrl
      }
    }
  }
  return patch
}

function askUsePrefsOnQuickAdd(prompt) {
  return new Promise((resolve) => {
    wx.showModal({
      title: prompt.title,
      content: prompt.content,
      cancelText: '否',
      confirmText: '是',
      success: (res) => resolve(!!res.confirm),
      fail: () => resolve(false)
    })
  })
}

Page({
  data: {
    categories: [],
    isWide: false,
    prefs: [],
    prefSummary: '',
    prefItems: [],
    prefCount: 0,
    prefExpanded: false,
    recoExpanded: false,
    activeCat: null,
    scrollInto: '',
    keyword: '',
    loading: true,
    storeName: '',
    storeNo: '',
    storeAddress: '',
    storePhone: '',
    storeLogo: '',
    tableName: '',
    tableNo: '',
    seatLabel: '桌号',
    hasTable: false,
    recoReady: false,
    recoStoreId: null,
    showMenuSearch: false,
    showMenuSmartTaste: false,
    showMenuGuessYouLike: false,
    cart: [],
    cartTotal: '0.00',
    cartCount: 0,
    invalidCount: 0,
    cartSheetOpen: false,
    cartThumbStyle: '',
    favoritesOnly: false,
    favSet: {},
    favCount: 0,
    isTakeawayChannel: false,
    tableOrderCount: 0,
    tableOrdersBrief: '',
    sharedCartOn: false,
    sharedIsHost: false,
    sharedMemberCount: 0
  },

  onLoad() {
    // onLoad 先于 onShow，提前发起菜单请求利用页面初始化时间窗口
    this._menuLoaded = false
    this._unsubDine = dineCtx.subscribeDineContext((ctx) => {
      this.applyDineContext(ctx)
    })
    this.applyMenuFlags()
    this.loadMenu()
  },

  onUnload() {
    if (this._searchTimer) clearTimeout(this._searchTimer)
    if (typeof this._unsubDine === 'function') {
      this._unsubDine()
      this._unsubDine = null
    }
  },

  onShow() {
    navBar.pinWechatNavBar()
    const metrics = media.layoutMetrics()
    const px = Math.round(Math.max(56, (metrics.thumbPx || 80) * 0.85))
    const cartThumbStyle = `width:${px}px;height:${px}px;`
    if (this.data.cartThumbStyle !== cartThumbStyle) {
      this.setData({ cartThumbStyle })
    }
    this.applyDineContext(dineCtx.currentDisplay())
    dineCtx.ensureDineContext().then((ctx) => {
      this.applyDineContext(ctx)
      this.applyMenuFlags(ctx && ctx.storeId)
    })
    this.loadPrefs(!app.globalData.prefsAt)
    this.loadFavorites()
    // 首次由 onLoad 拉；后续回到页面时走 loadMenu（内部 TTL 可跳过）
    if (this._menuLoaded) {
      this.loadMenu()
    } else {
      this._menuLoaded = true
    }
    const openSheet = !!(app.globalData && app.globalData._openCartSheet)
    if (app.globalData) app.globalData._openCartSheet = false
    this.refreshCartBar({ openSheet })
    this.syncSharedCartMode()
    this.loadTableOrdersBrief()
  },

  onHide() {
    sharedCart.stopPolling()
  },

  applyDineContext(ctx) {
    if (!ctx) return
    dineCtx.applyPageDineFields(this, ctx)
  },

  /** 堂食绑桌：加入共享车并轮询；外带/未绑桌停用 */
  async syncSharedCartMode() {
    if (!sharedCart.shouldUseShared()) {
      sharedCart.stopPolling()
      this.setData({ sharedCartOn: false, sharedIsHost: false, sharedMemberCount: 0 })
      return
    }
    try {
      await sharedCart.joinAndPull()
      const meta = sharedCart.getMeta()
      this.setData({
        sharedCartOn: !!meta.enabled,
        sharedIsHost: !!meta.is_host,
        sharedMemberCount: meta.member_count || 0
      })
      sharedCart.startPolling(2800)
      await this.refreshCartBar({ openSheet: false })
    } catch (_) {
      this.setData({ sharedCartOn: false })
    }
  },

  /** 本桌已下单：优先全桌聚合；失败则回退本人订单 */
  async loadTableOrdersBrief() {
    const ctx = dineCtx.currentDisplay() || {}
    const table = ctx.table || {}
    const tid = table.id
    if (!ctx.hasTable || !tid || ctx.isTakeawayChannel) {
      if (this.data.tableOrderCount || this.data.tableOrdersBrief) {
        this.setData({ tableOrderCount: 0, tableOrdersBrief: '' })
      }
      return
    }
    const now = Date.now()
    if (this._tableOrdersAt && now - this._tableOrdersAt < 12000 && this._tableOrdersTid === tid) {
      return
    }
    try {
      if (sharedCart.shouldUseShared()) {
        const res = await sharedCart.fetchTableOrders()
        const count = (res && res.count) || ((res && res.items) || []).length
        let brief = ''
        if (count === 1) {
          const o = (res.items || [])[0] || {}
          brief = o.status_label || o.status || '进行中'
        } else if (count > 1) {
          brief = `${count} 笔进行中（全桌）`
        }
        this._tableOrdersAt = Date.now()
        this._tableOrdersTid = tid
        this.setData({ tableOrderCount: count, tableOrdersBrief: brief })
        return
      }
      await auth.ensureLogin()
      const params = { page: 1, page_size: 20 }
      if (ctx.storeId) params.store_id = ctx.storeId
      const res = await api.get('/orders/mine', params)
      const active = { PAID: 1, CONFIRMED: 1, PREPARING: 1, COOKING: 1, READY: 1 }
      const items = ((res && res.items) || []).filter((o) => {
        const st = String((o && o.status) || '').toUpperCase()
        if (!active[st]) return false
        const ot = (o && o.table) || {}
        return Number(ot.id || o.table_id) === Number(tid)
      })
      const count = items.length
      let brief = ''
      if (count === 1) {
        brief = items[0].status_label || items[0].status || '进行中'
      } else if (count > 1) {
        brief = `${count} 笔进行中`
      }
      this._tableOrdersAt = Date.now()
      this._tableOrdersTid = tid
      this.setData({ tableOrderCount: count, tableOrdersBrief: brief })
    } catch (_) {
      /* 静默：不影响点餐 */
    }
  },

  goOrders() {
    wx.switchTab({ url: '/pages/orders/orders' })
  },

  applyMenuFlags(storeId) {
    const apply = () => {
      const flags = clientConfig.menuPageFlags()
      const patch = { ...flags }
      const hadKeyword = !!this.data.keyword
      if (!flags.showMenuSearch && hadKeyword) {
        patch.keyword = ''
      }
      const d = this.data
      if (
        d.showMenuSearch === patch.showMenuSearch
        && d.showMenuSmartTaste === patch.showMenuSmartTaste
        && d.showMenuGuessYouLike === patch.showMenuGuessYouLike
        && (patch.keyword === undefined || d.keyword === patch.keyword)
      ) {
        return
      }
      this.setData(patch, () => {
        if (!flags.showMenuSearch && hadKeyword) {
          this.loadMenu()
        }
      })
    }
    const sid = storeId || this.data.storeId || null
    clientConfig.loadClientConfig(false, sid).then(apply).catch(() => { })
  },

  onTableChange(e) {
    const ctx = e.detail
    if (!ctx) return
    this.applyDineContext(ctx)
    clientConfig.invalidateClientConfig()
    this._catsCache = null
    this._lastProductItems = null
    this._lastMenuFetchAt = 0
    this._menuFetchStoreId = null
    this._menuVisibilityDay = null
    this._menuAuthFinger = null
    this._favLoadedAt = 0
    this.applyMenuFlags(ctx.storeId)
    this._tableOrdersAt = 0
    this.loadMenu(true)
    this.loadFavorites(true)
    this.refreshCartBar({ openSheet: false, forceCatalog: true })
    this.loadTableOrdersBrief()
  },

  onPullDownRefresh() {
    this._catsCache = null
    this._lastMenuFetchAt = 0
    this._menuFetchStoreId = null
    this._menuVisibilityDay = null
    this._menuAuthFinger = null
    Promise.all([
      dineCtx.ensureDineContext({ force: true }).then((ctx) => {
        this.applyDineContext(ctx)
        const sid = (ctx && ctx.storeId) || null
        return clientConfig.loadClientConfig(true, sid).then(() => {
          const flags = clientConfig.menuPageFlags()
          const patch = { ...flags }
          const hadKeyword = !!this.data.keyword
          if (!flags.showMenuSearch && hadKeyword) {
            patch.keyword = ''
          }
          this.setData(patch, () => {
            if (!flags.showMenuSearch && hadKeyword) {
              this.loadMenu(true)
            }
          })
        }).catch(() => { })
      }),
      this.loadPrefs(true),
      this.loadFavorites(true),
      this.loadMenu(true),
      this.refreshCartBar({ openSheet: false, forceCatalog: true })
    ]).finally(() => wx.stopPullDownRefresh())
  },

  storeIdForCart() {
    try {
      const ctx = dineCtx.currentDisplay()
      if (ctx && ctx.storeId) return ctx.storeId
    } catch (_) { }
    const g = app && app.globalData
    if (!g) return null
    return (g.table && g.table.store_id)
      || (g.store && (g.store.id || g.store.store_id))
      || null
  },

  async refreshCartBar(opts) {
    const openSheet = !!(opts && opts.openSheet)
    const forceCatalog = !!(opts && opts.forceCatalog)
    const rawCart = auth.getCart()
    let products = this._lastProductItems || []
    let categories = (this._catsCache && this._catsCache.cats) || this.data.categories || []
    let complete = !!(products && products.length)
    try {
      const sid = this.storeIdForCart()
      const needCatalog = !!sid && (forceCatalog || (rawCart.length > 0 && !products.length))
      if (needCatalog) {
        const catalog = await productApi.loadStoreCatalog(api, sid, { preferCache: !forceCatalog })
        products = (catalog && catalog.items) || products
        categories = (catalog && catalog.categories) || categories
        complete = !!(catalog && catalog.complete)
        if (catalog && catalog.items) {
          this._lastProductItems = catalog.items
          if (app.globalData) {
            app.globalData._sharedCatalog = {
              storeId: sid,
              items: catalog.items,
              categories: (catalog && catalog.categories) || categories,
              at: Date.now()
            }
          }
        }
      }
    } catch (_) { }

    const { list, invalidCount } = cartUtil.reconcileWithProducts(rawCart, products, categories, { complete })
    const cart = list.map((raw) => {
      const item = cartUtil.normalizeItem({ ...raw })
      return {
        ...item,
        // wx:key 需要稳定唯一值：餐品 ID + 排序后的规格 ID（与 cartUtil.itemKey 同源；mergeCartItem 保证同键只有一条）
        cartKey: `${item.product_id}:${(item.option_ids || []).slice().sort((a, b) => a - b).join(',')}`,
        imageUrl: media.resolveUrl(item.main_image || item.imageUrl || '')
      }
    })
    const validItems = cart.filter((i) => !i.invalid)
    const { total, count } = cartUtil.cartSummary(validItems)
    auth.saveCart(cart)
    const patch = {
      cart,
      cartTotal: total,
      cartCount: count,
      invalidCount
    }
    if (!cart.length) patch.cartSheetOpen = false
    else if (openSheet) patch.cartSheetOpen = true
    this.setData(patch)
  },

  closeCartSheet() {
    this.setData({ cartSheetOpen: false })
  },

  /** 左下角：收起时打开点餐明细，展开时关闭继续点餐 */
  onFooterLeftTap() {
    if (!this.data.cart.length) return
    this.setData({ cartSheetOpen: !this.data.cartSheetOpen })
  },

  changeCartQty(e) {
    const { index, delta } = e.currentTarget.dataset
    const cart = auth.getCart()
    const item = cart[index]
    if (!item || item.invalid) return
    const qty = (item.quantity || 1) + parseInt(delta, 10)
    if (qty <= 0) {
      cart.splice(index, 1)
      try { tracker.cartRemove(Number(item.product_id), { scene: 'menu', category_id: item.category_id || null }) } catch (_) { }
    } else if (qty > 999) {
      wx.showToast({ title: '单品最多 999 份', icon: 'none' })
      return
    } else {
      cartUtil.normalizeItem(item)
      item.quantity = qty
      item.subtotal = (parseFloat(item.unit_price) * qty).toFixed(2)
    }
    auth.saveCart(cart)
    this.refreshCartBar({ openSheet: !!(this.data.cartSheetOpen && cart.length) })
  },

  removeCartItem(e) {
    const index = e.currentTarget.dataset.index
    const cart = auth.getCart()
    const removed = cart[index]
    if (removed == null) return
    cart.splice(index, 1)
    auth.saveCart(cart)
    try { tracker.cartRemove(Number(removed.product_id), { scene: 'menu', category_id: removed.category_id || null }) } catch (_) { }
    this.refreshCartBar({ openSheet: !!(this.data.cartSheetOpen && cart.length) })
  },

  clearInvalidCart() {
    const cart = auth.getCart().filter((i) => !i.invalid)
    auth.saveCart(cart)
    wx.showToast({ title: '已清除失效餐品', icon: 'success' })
    this.refreshCartBar({ openSheet: !!(cart.length) })
  },

  goCheckout() {
    if (this.data.invalidCount > 0) {
      wx.showModal({
        title: '有餐品不可下单',
        content: `${this.data.invalidCount} 件餐品已失效或库存不足，请先清除后再结算`,
        confirmText: '清除失效',
        cancelText: '返回',
        success: (res) => {
          if (res.confirm) this.clearInvalidCart()
        }
      })
      return
    }
    if (!this.data.cart.length || this.data.cartCount === 0) {
      wx.showToast({ title: '请先点餐', icon: 'none' })
      return
    }
    wx.navigateTo({ url: '/packageOrder/confirm/confirm' })
  },

  async scanTable() {
    const ctx = await dineCtx.scanAndBindTable()
    if (ctx) {
      this.applyDineContext(ctx)
      clientConfig.invalidateClientConfig()
      this._catsCache = null
      this._lastProductItems = null
      this._lastMenuFetchAt = 0
      this._menuFetchStoreId = null
      this._menuVisibilityDay = null
      this._menuAuthFinger = null
      this.applyMenuFlags(ctx.storeId)
      this.loadMenu(true)
      this.refreshCartBar({ openSheet: false, forceCatalog: true })
    }
  },

  async loadPrefs(force = false) {
    const g = app.globalData || {}
    const now = Date.now()
    const skipLogin = !force
      && g.prefsAt
      && g.prefsView
      && (now - g.prefsAt) < PREFS_SKIP_LOGIN_MS
    const fingerBefore = menuAuthFinger()
    if (!skipLogin) {
      try {
        await auth.ensureLogin()
      } catch (_) { }
    }
    // 登录态变化（尤其宾客→登录）须立刻重拉菜单，否则生日菜要等下次 onShow/TTL
    const fingerAfter = menuAuthFinger()
    if (
      fingerAfter !== fingerBefore
      || (this._menuAuthFinger != null && fingerAfter !== this._menuAuthFinger)
    ) {
      this.loadMenu(true)
    }
    try {
      const view = await prefUtil.loadCustomerPrefs(app, api, { force })
      const prefCount = (view && view.prefCount) || 0
      this.setData({
        prefs: (view && view.prefs) || [],
        prefSummary: (view && view.prefSummary) || '',
        prefItems: (view && view.prefItems) || [],
        prefCount,
        prefExpanded: prefCount > 3 ? this.data.prefExpanded : false
      })
    } catch (_) { }
  },

  buildCategories(cats, items, metrics, keyword) {
    const m = metrics || media.layoutMetrics()
    const kw = (keyword || '').trim().toLowerCase()
    const favOnly = !!this.data.favoritesOnly
    const favSet = this._favSet || {}
    const catList = normalizeList(cats)
    const productList = normalizeList(items)
    return catList
      .map((c) => {
        if (!c) return null
        const products = productList
          .filter((p) => p && Number(p.category_id) === Number(c.id))
          .filter((p) => {
            if (!kw) return true
            return (
              String(p.name || '').toLowerCase().includes(kw)
              || String(p.description || '').toLowerCase().includes(kw)
            )
          })
          .filter((p) => !favOnly || !!favSet[String(p.id)])
          .map((p) => {
            const avail = cartUtil.productAvailability(p)
            return {
              ...media.decorateProduct(p, m),
              fav: !!favSet[String(p.id)],
              unavailable: !avail.available,
              soldOut: !!avail.soldOut,
              unavailableLabel: avail.label || ''
            }
          })
          .sort((a, b) => Number(!!a.unavailable) - Number(!!b.unavailable))
        return { ...c, products }
      })
      .filter(Boolean)
      .filter((c) => c.products.length > 0 || (!kw && !favOnly))
  },

  async loadMenu(force) {
    const sid = (app.globalData.table && app.globalData.table.store_id)
      || (app.globalData.store && app.globalData.store.id)
      || null
    if (!sid) {
      wx.showToast({ title: '请先扫桌码选择门店', icon: 'none' })
      this.setData({ loading: false })
      return
    }
    const cacheKey = String(sid)
    if (this._menuInflight && this._menuInflightStoreId === cacheKey) {
      return this._menuInflight
    }
    const authFinger = menuAuthFinger()
    // 不再用页面 TTL 跳过拉取：每次 onShow/loadMenu 走「缓存先显 + version 探测」，
    // 后台改价/上下架可在回到菜单时立刻生效；同 version 仅轻量回包。

    const run = async () => {
      const now = Date.now()
      const metrics = media.layoutMetrics()
      const markFetched = (items, categories, visibilityDay) => {
        this._lastMenuFetchAt = Date.now()
        this._menuFetchStoreId = sid
        this._menuVisibilityDay = visibilityDay || productApi.localVisibilityDay()
        this._menuAuthFinger = menuAuthFinger()
        if (app.globalData && items) {
          app.globalData._sharedCatalog = {
            storeId: sid,
            items,
            categories: categories || [],
            at: this._lastMenuFetchAt
          }
        }
      }

      const applyBundle = async (bundle) => {
        const catList = normalizeList(bundle.categories)
        const items = normalizeList(bundle.products)
        const vday = bundle.visibility_day || productApi.localVisibilityDay()
        productApi.writeCachedMenu(sid, {
          version: bundle.version,
          visibility_day: vday,
          categories: catList,
          products: items
        })
        this._catsCache = { key: cacheKey, at: now, cats: catList, metrics }
        this._lastProductItems = items
        const categories = this.buildCategories(catList, items, metrics, this.data.keyword)
        await this.applyCategories(categories, metrics)
        markFetched(items, catList, vday)
        this.setData({ recoReady: true, recoStoreId: sid })
      }

      // 0) 消费 app.js 预拉取：仅当登录态与预拉时一致才跳过二次请求，
      // 避免冷启动宾客菜单把已登录用户的生日菜「吃掉」后直接 return。
      if (!force && app.globalData._menuPrefetch && app.globalData._menuPrefetch.storeId === cacheKey) {
        const prefetch = app.globalData._menuPrefetch
        app.globalData._menuPrefetch = null
        if (String(prefetch.authFinger || '0') === authFinger) {
          try {
            const bundle = await prefetch.promise
            if (bundle && bundle.changed !== false && bundle.categories) {
              await applyBundle(bundle)
              return
            }
          } catch (_) { }
        }
      }

      // 1) 本地缓存先渲染（弱网兜底 + 首屏提速），不弹骨架
      const cached = productApi.readCachedMenu(sid)
      if (cached && cached.categories) {
        const cachedItems = normalizeList(cached.products)
        this._catsCache = { key: cacheKey, at: now, cats: cached.categories, metrics }
        this._lastProductItems = cachedItems
        const cachedView = this.buildCategories(cached.categories, cachedItems, metrics, this.data.keyword)
        await this.applyCategories(cachedView, metrics)
      } else if (!this.data.categories.length) {
        this.setData({ loading: true })
      }

      // 2) 后台静默版本校验：changed:false 且同日才保留本地；跨日强制全量
      try {
        let bundle = await productApi.fetchMenuBundle(api, sid, force ? null : (cached && cached.version))
        if (bundle.changed === false) {
          const day = bundle.visibility_day || ''
          const sameDay = !day || day === productApi.localVisibilityDay()
          if (sameDay && this._lastProductItems && this._lastProductItems.length) {
            if (this._catsCache) this._catsCache.at = now
            markFetched(this._lastProductItems, (this._catsCache && this._catsCache.cats) || [], day)
            this.setData({ recoReady: true, recoStoreId: sid })
            return
          }
          bundle = await productApi.fetchMenuBundle(api, sid, null)
        }
        if (bundle && bundle.changed !== false && bundle.products != null) {
          await applyBundle(bundle)
        }
      } catch (e) {
        // 弱网/接口失败：已有缓存就静默保留旧菜单，无缓存才提示
        if (!cached) {
          wx.showToast({ title: (e && e.message) || '加载失败', icon: 'none' })
          this.setData({ loading: false })
        }
      }
    }

    this._menuInflightStoreId = cacheKey
    this._menuInflight = run().finally(() => {
      if (this._menuInflightStoreId === cacheKey) {
        this._menuInflight = null
        this._menuInflightStoreId = null
      }
    })
    return this._menuInflight
  },

  applyCategories(categories, metrics) {
    const m = metrics || media.layoutMetrics()
    const all = categories || []
    const first = sliceMenuForFirstPaint(all, 40)
    const activeCat = (all[0] && all[0].id) || null
    this.setData({
      categories: first,
      isWide: !!m.isWide,
      activeCat,
      scrollInto: activeCat ? `cat-${activeCat}` : '',
      loading: false
    }, () => this.scheduleMeasureCats())
    const gen = (this._imgGen = (this._imgGen || 0) + 1)
    if (first !== all) {
      setTimeout(() => {
        if (this._imgGen !== gen) return
        this.setData({ categories: all }, () => this.scheduleMeasureCats())
      }, 60)
    }
    const flat = []
    all.forEach((c) => {
      ; (c.products || []).forEach((p) => flat.push(p))
    })
    // 图片在首屏之后限流下载，不堵住购物车拉菜单
    media.hydrateImageUrls(flat, 'imageUrl').then(() => {
      if (this._imgGen !== gen) return
      const patch = buildImageUrlPatch(this.data.categories, all)
      if (patch) {
        this.setData(patch, () => this.scheduleMeasureCats())
      } else {
        this.setData({ categories: all }, () => this.scheduleMeasureCats())
      }
    }).catch(() => { })
  },

  onSearch(e) {
    const keyword = (e.detail && e.detail.value) || ''
    this.setData({ keyword })
    if (this._searchTimer) clearTimeout(this._searchTimer)
    if (!this._catsCache || !this._lastProductItems) {
      this.loadMenu()
      return
    }
    // P3-7: 搜索防抖300ms，避免每次按键都重建分类列表
    this._searchTimer = setTimeout(() => {
      const { cats, metrics } = this._catsCache
      const items = this._lastProductItems
      const rebuilt = this.buildCategories(cats, items, metrics, keyword)
      const activeCat = (rebuilt[0] && rebuilt[0].id) || null
      this.setData({
        categories: rebuilt,
        activeCat,
        scrollInto: activeCat ? `cat-${activeCat}` : ''
      }, () => this.scheduleMeasureCats())
    }, 300)
  },

  onCatTap(e) {
    const id = e.currentTarget.dataset.id
    this._catScrollLockUntil = Date.now() + 450
    this.setData({ activeCat: id, scrollInto: `cat-${id}` })
    // 点击跳转后重测分区偏移，避免首屏高度变化导致联动漂移
    setTimeout(() => this.measureCatOffsets(), 480)
  },

  toggleRecoExpand() {
    this.setData({ recoExpanded: !this.data.recoExpanded }, () => {
      setTimeout(() => this.measureCatOffsets(), 80)
    })
  },

  onMenuBodyScrollTop() {
    const first = this.data.categories && this.data.categories[0]
    if (first && first.id !== this.data.activeCat) {
      this.setData({ activeCat: first.id })
    }
  },

  onMenuBodyScroll(e) {
    if (this._catScrollLockUntil && Date.now() < this._catScrollLockUntil) return
    const scrollTop = (e && e.detail && e.detail.scrollTop) || 0
    const offsets = this._catOffsets
    if (!offsets || !offsets.length) return
    let active = offsets[0].id
    for (let i = 0; i < offsets.length; i += 1) {
      if (scrollTop + 24 >= offsets[i].top) active = offsets[i].id
      else break
    }
    if (active != null && active !== this.data.activeCat) {
      this.setData({ activeCat: active })
    }
  },

  measureCatOffsets() {
    const cats = this.data.categories || []
    if (!cats.length) {
      this._catOffsets = []
      return
    }
    const query = wx.createSelectorQuery().in(this)
    query.select('.menu-body').boundingClientRect()
    query.select('.menu-body').scrollOffset()
    cats.forEach((c) => {
      query.select(`#cat-${c.id}`).boundingClientRect()
    })
    query.exec((res) => {
      if (!res || !res[0] || !res[1]) return
      const bodyTop = res[0].top || 0
      const scrollTop = (res[1] && res[1].scrollTop) || 0
      const offsets = []
      for (let i = 0; i < cats.length; i += 1) {
        const rect = res[2 + i]
        if (!rect) continue
        offsets.push({
          id: cats[i].id,
          top: Math.max(0, (rect.top - bodyTop) + scrollTop)
        })
      }
      this._catOffsets = offsets
    })
  },

  openProduct(e) {
    const pid = Number(e.currentTarget.dataset.id)
    const found = (this._lastProductItems || []).find((p) => String(p.id) === String(pid))
    const avail = cartUtil.productAvailability(found || { id: pid, status: 'ON_SALE' })
    if (found && !avail.available) {
      wx.showToast({ title: avail.label || '暂不可售', icon: 'none' })
      return
    }
    try { tracker.click(pid, { scene: 'menu' }) } catch (_) { }
    wx.navigateTo({ url: `/packageOrder/product/product?id=${pid}` })
  },

  // ---- 推荐位事件（埋点 CLICK/EXPOSE 已在组件内发出）----
  onRecommendOpen(e) {
    const pid = Number(e.detail && e.detail.id)
    if (pid) wx.navigateTo({ url: `/packageOrder/product/product?id=${pid}` })
  },

  onRecommendAdd(e) {
    const item = (e.detail || {})
    const found = (this._lastProductItems || []).find((p) => String(p.id) === String(item.id))
    // 复用 quickAdd 主链路（规格校验/口味偏好）；查不到餐品时安全起见走详情页
    this.quickAdd({
      currentTarget: {
        dataset: {
          id: item.id,
          name: item.name,
          price: item.price,
          image: item.image,
          hasOptions: found ? !!found.has_options : true,
          noMemberDiscount: found ? !!found.no_member_discount : false,
          categoryId: found && found.category_id != null ? found.category_id : item.category_id,
          trackScene: 'recommend'
        }
      }
    })
  },

  fetchProductDetail(id) {
    if (!this._productDetailCache) this._productDetailCache = {}
    const key = String(id)
    if (this._productDetailCache[key]) return Promise.resolve(this._productDetailCache[key])
    return api.get(`/products/${id}`, {}, false).then((product) => {
      this._productDetailCache[key] = product
      return product
    })
  },

  addSimpleToCart(entry) {
    const cart = cartUtil.mergeCartItem(auth.getCart(), entry)
    auth.saveCart(cart)
    try {
      tracker.cartAdd(Number(entry.product_id), {
        scene: entry._trackScene || 'menu',
        category_id: entry.category_id || null,
        payload: { qty: Number(entry.quantity) || 1 }
      })
    } catch (_) { }
    this.refreshCartBar({ openSheet: true })
  },

  assertCanAddProduct(productLike) {
    const cats = (this._catsCache && this._catsCache.cats) || this.data.categories || []
    const products = this._lastProductItems || []
    const check = cartUtil.checkCategoryRequire(productLike, auth.getCart(), products, cats)
    if (!check.ok) {
      wx.showToast({ title: check.message, icon: 'none', duration: 2500 })
      return false
    }
    return true
  },

  async quickAdd(e) {
    const ds = e.currentTarget.dataset
    const id = Number(ds.id)
    const hasOptions = ds.hasOptions === true || ds.hasOptions === 'true' || ds.hasOptions === 1 || ds.hasOptions === '1'
    const noMemberDiscount = ds.noMemberDiscount === true || ds.noMemberDiscount === 'true' || ds.noMemberDiscount === 1 || ds.noMemberDiscount === '1'
    const listItem = (this._lastProductItems || []).find((p) => String(p.id) === String(id)) || {
      id,
      name: ds.name,
      category_id: ds.categoryId != null ? Number(ds.categoryId) : undefined,
      require_category_ids: []
    }
    const avail = cartUtil.productAvailability(listItem)
    if (!avail.available) {
      wx.showToast({ title: avail.label || '暂不可售', icon: 'none' })
      return
    }
    if (!this.assertCanAddProduct(listItem)) return

    if (!hasOptions) {
      const unit = parseFloat(ds.price) || 0
      this.addSimpleToCart({
        product_id: id,
        name: ds.name,
        quantity: 1,
        option_ids: [],
        option_labels: [],
        option_text: '',
        unit_price: unit,
        subtotal: unit.toFixed(2),
        imageUrl: ds.image || '',
        main_image: '',
        no_member_discount: noMemberDiscount,
        category_id: listItem.category_id,
        require_category_ids: listItem.require_category_ids || [],
        require_product_ids: listItem.require_product_ids || [],
        _trackScene: ds.trackScene || 'menu'
      })
      wx.showToast({ title: '已加入', icon: 'success' })
      return
    }

    try {
      wx.showLoading({ title: '加入中', mask: true })
      const product = await this.fetchProductDetail(id)
      if (!this.assertCanAddProduct(product)) {
        wx.hideLoading()
        return
      }
      const prefs = this.data.prefs || app.globalData.prefs || []
      const prefPrompt = prefUtil.buildQuickAddPrefPrompt(product, prefs)
      let usePrefs = false
      if (prefPrompt) {
        wx.hideLoading()
        usePrefs = await askUsePrefsOnQuickAdd(prefPrompt)
        wx.showLoading({ title: '加入中', mask: true })
      }
      const { selected, multi } = prefUtil.resolveQuickAddSelections(product, prefs, usePrefs)
      if (!prefUtil.selectionsComplete(product, selected, multi)) {
        wx.hideLoading()
        wx.navigateTo({ url: `/packageOrder/product/product?id=${id}` })
        return
      }
      const unitPrice = cartUtil.calcUnitPrice(product, selected, multi)
      const optionLabels = cartUtil.buildOptionLabels(product, selected, multi)
      this.addSimpleToCart({
        product_id: id,
        name: product.name || ds.name,
        quantity: 1,
        option_ids: cartUtil.collectOptionIds(product, selected, multi),
        option_labels: optionLabels,
        option_text: optionLabels.join('、'),
        unit_price: unitPrice,
        subtotal: unitPrice.toFixed(2),
        main_image: product.main_image || '',
        imageUrl: media.resolveUrl(product.main_image, media.imageVersion(product)) || ds.image || '',
        no_member_discount: noMemberDiscount || !!product.no_member_discount,
        category_id: product.category_id,
        require_category_ids: product.require_category_ids || [],
        require_product_ids: product.require_product_ids || [],
        _trackScene: ds.trackScene || 'menu'
      })
      wx.hideLoading()
      wx.showToast({ title: usePrefs ? '已按爱好加入' : '已加入', icon: 'success' })
    } catch (err) {
      wx.hideLoading()
      wx.showToast({ title: (err && err.message) || '加入失败', icon: 'none' })
    }
  },

  togglePrefExpand() {
    const prefCount = this.data.prefCount || 0
    if (prefCount <= 3) {
      if (this.data.prefExpanded) this.setData({ prefExpanded: false })
      return
    }
    this.setData({ prefExpanded: !this.data.prefExpanded })
  },

  // ---- 菜品收藏 ----
  /** 写入收藏集合；收藏数为 0 时自动退出「仅看收藏」，避免菜单被滤空且无法切回 */
  applyFavSet(favSet, opts) {
    const next = favSet && typeof favSet === 'object' ? favSet : {}
    const favCount = Object.keys(next).length
    const patch = { favSet: next, favCount }
    // 仅收藏筛选开启且已无收藏：必须退出筛选，否则 categories 为空且筛选条因 favCount=0 被隐藏
    if (favCount === 0 && this.data.favoritesOnly) patch.favoritesOnly = false
    this._favSet = next
    const rebuild = !(opts && opts.skipRebuild)
    this.setData(patch, () => {
      if (rebuild) this.rebuildMenuView()
    })
    return favCount
  },

  async loadFavorites(force = false) {
    const now = Date.now()
    const sid = (app.globalData.table && app.globalData.table.store_id)
      || (app.globalData.store && app.globalData.store.id)
      || null
    const sidKey = String(sid || '')
    if (
      !force
      && this._favLoadedAt
      && this._favLoadedStoreId === sidKey
      && now - this._favLoadedAt < 60 * 1000
    ) {
      return
    }
    try {
      await auth.ensureLogin()
      const params = {}
      if (sid) params.store_id = sid
      const res = await api.get('/miniprogram/favorites', params)
      const favSet = {}
        ; (Array.isArray(res) ? res : (res && res.items) || []).forEach((x) => {
          const pid = x && typeof x === 'object' ? (x.product_id != null ? x.product_id : x.id) : x
          if (pid != null) favSet[String(pid)] = true
        })
      this._favLoadedAt = Date.now()
      this._favLoadedStoreId = sidKey
      // 首屏尚未建好 categories 时也更新 favSet；有菜单时再重建视图
      this.applyFavSet(favSet, { skipRebuild: !this.data.categories.length })
    } catch (_) {
      // 未登录 / 失败静默
    }
  },

  async toggleFav(e) {
    const id = Number(e.currentTarget.dataset.id)
    if (!id || this._favToggling) return
    const prevSet = this._favSet || {}
    const wasFav = !!prevSet[String(id)]
    const nextSet = { ...prevSet }
    if (wasFav) delete nextSet[String(id)]
    else nextSet[String(id)] = true
    this._favToggling = true
    this.applyFavSet(nextSet)
    try {
      await auth.ensureLogin()
      if (wasFav) await api.delete(`/miniprogram/favorites/${id}`, {})
      else await api.post(`/miniprogram/favorites/${id}`, {})
    } catch (err) {
      // 失败回滚并静默
      const rollback = { ...(this._favSet || {}) }
      if (wasFav) rollback[String(id)] = true
      else delete rollback[String(id)]
      this.applyFavSet(rollback)
      console.warn('收藏操作失败', err)
    } finally {
      this._favToggling = false
    }
  },

  toggleFavFilter() {
    // 无收藏时不允许进入「仅看收藏」，避免空菜单死局
    if (!this.data.favoritesOnly && !(this.data.favCount > 0)) return
    this.setData({ favoritesOnly: !this.data.favoritesOnly }, () => this.rebuildMenuView())
  },

  /** 空态点击：仅在「仅看收藏」时退出筛选 */
  exitFavFilter() {
    if (!this.data.favoritesOnly) return
    this.setData({ favoritesOnly: false }, () => this.rebuildMenuView())
  },

  /** 基于 _catsCache/_lastProductItems 重建当前菜单视图（搜索词 + 收藏筛选） */
  rebuildMenuView() {
    if (!this._catsCache || !this._lastProductItems) {
      this.loadMenu()
      return
    }
    // 防御：仅收藏且已无收藏时先同步退出，保证 buildCategories 读到正确标志
    const prevFavOnly = !!this.data.favoritesOnly
    const favoritesOnly = prevFavOnly && (this.data.favCount > 0)
    if (favoritesOnly !== prevFavOnly) this.data.favoritesOnly = favoritesOnly
    const { cats, metrics } = this._catsCache
    const items = this._lastProductItems
    const rebuilt = this.buildCategories(cats, items, metrics, this.data.keyword)
    const activeCat = (rebuilt[0] && rebuilt[0].id) || null
    const patch = {
      categories: rebuilt,
      activeCat,
      scrollInto: activeCat ? `cat-${activeCat}` : ''
    }
    if (favoritesOnly !== prevFavOnly) patch.favoritesOnly = favoritesOnly
    this.setData(patch, () => this.scheduleMeasureCats())
  },

  scheduleMeasureCats() {
    if (this._measureCatsTimer) clearTimeout(this._measureCatsTimer)
    this._measureCatsTimer = setTimeout(() => {
      this._measureCatsTimer = null
      this.measureCatOffsets()
    }, 60)
  },

  goMember() {
    wx.switchTab({ url: '/pages/member/member' })
  }
})
