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
    favCount: 0
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
  },

  applyDineContext(ctx) {
    if (!ctx) return
    dineCtx.applyPageDineFields(this, ctx)
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
    this.loadMenu(true)
    this.loadFavorites(true)
    this.refreshCartBar({ openSheet: false, forceCatalog: true })
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
        // wx:key 需要稳定唯一值：商品 ID + 排序后的规格 ID（与 cartUtil.itemKey 同源；mergeCartItem 保证同键只有一条）
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
    wx.showToast({ title: '已清除失效商品', icon: 'success' })
    this.refreshCartBar({ openSheet: !!(cart.length) })
  },

  goCheckout() {
    if (this.data.invalidCount > 0) {
      wx.showModal({
        title: '有商品不可下单',
        content: `${this.data.invalidCount} 件商品已失效或库存不足，请先清除后再结算`,
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
          .map((p) => ({ ...media.decorateProduct(p, m), fav: !!favSet[String(p.id)] }))
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
    })
    const gen = (this._imgGen = (this._imgGen || 0) + 1)
    if (first !== all) {
      setTimeout(() => {
        if (this._imgGen !== gen) return
        this.setData({ categories: all })
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
        this.setData(patch)
      } else {
        this.setData({ categories: all })
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
      })
    }, 300)
  },

  onCatTap(e) {
    const id = e.currentTarget.dataset.id
    this.setData({ activeCat: id, scrollInto: `cat-${id}` })
  },

  openProduct(e) {
    const pid = Number(e.currentTarget.dataset.id)
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
    // 复用 quickAdd 主链路（规格校验/口味偏好）；查不到商品时安全起见走详情页
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
      this._favSet = favSet
      this._favLoadedAt = Date.now()
      this._favLoadedStoreId = sidKey
      this.setData({ favSet, favCount: Object.keys(favSet).length })
      if (this.data.categories.length) this.rebuildMenuView()
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
    this._favSet = nextSet
    this.setData({ favSet: nextSet, favCount: Object.keys(nextSet).length })
    this.rebuildMenuView()
    try {
      await auth.ensureLogin()
      if (wasFav) await api.delete(`/miniprogram/favorites/${id}`, {})
      else await api.post(`/miniprogram/favorites/${id}`, {})
    } catch (err) {
      // 失败回滚并静默
      const rollback = { ...(this._favSet || {}) }
      if (wasFav) rollback[String(id)] = true
      else delete rollback[String(id)]
      this._favSet = rollback
      this.setData({ favSet: rollback, favCount: Object.keys(rollback).length })
      this.rebuildMenuView()
      console.warn('收藏操作失败', err)
    } finally {
      this._favToggling = false
    }
  },

  toggleFavFilter() {
    this.setData({ favoritesOnly: !this.data.favoritesOnly }, () => this.rebuildMenuView())
  },

  /** 基于 _catsCache/_lastProductItems 重建当前菜单视图（搜索词 + 收藏筛选） */
  rebuildMenuView() {
    if (!this._catsCache || !this._lastProductItems) {
      this.loadMenu()
      return
    }
    const { cats, metrics } = this._catsCache
    const items = this._lastProductItems
    const rebuilt = this.buildCategories(cats, items, metrics, this.data.keyword)
    const activeCat = (rebuilt[0] && rebuilt[0].id) || null
    this.setData({
      categories: rebuilt,
      activeCat,
      scrollInto: activeCat ? `cat-${activeCat}` : ''
    })
  },

  goMember() {
    wx.switchTab({ url: '/pages/member/member' })
  }
})
