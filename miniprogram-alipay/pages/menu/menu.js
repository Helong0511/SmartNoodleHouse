const api = require('../../utils/api')
const auth = require('../../utils/auth')
const cartUtil = require('../../utils/cart')
const media = require('../../utils/media')
const prefUtil = require('../../utils/preferences')
const dineCtx = require('../../utils/context')
const tracker = require('../../utils/tracker')
const productApi = require('../../utils/products')
const clientConfig = require('../../utils/clientConfig')
const navBar = require('../../utils/navBar')

const MENU_REFRESH_TTL_MS = 30 * 1000
const PREFS_SKIP_LOGIN_MS = 60 * 1000

function appGlobal() {
  try {
    const a = getApp()
    return (a && a.globalData) || {}
  } catch (_) {
    return {}
  }
}

function normalizeList(data) {
  if (Array.isArray(data)) return data
  if (data && Array.isArray(data.items)) return data.items
  return []
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
    my.confirm({
      title: prompt.title,
      content: prompt.content,
      confirmButtonText: '是',
      cancelButtonText: '否',
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
    navBar.pinAlipayNavBar()
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
    const g = appGlobal()
    this.loadPrefs(!g.prefsAt)
    this.loadFavorites()
    // 首次由 onLoad 拉；后续回到页面时走 loadMenu（内部 TTL 可跳过）
    if (this._menuLoaded) {
      this.loadMenu()
    } else {
      this._menuLoaded = true
    }
    const openSheet = !!g._openCartSheet
    try {
      const app = getApp()
      if (app && app.globalData) app.globalData._openCartSheet = false
    } catch (_) { }
    this.refreshCartBar({ openSheet })
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

  applyDineContext(ctx) {
    if (!ctx) return
    dineCtx.applyPageDineFields(this, ctx)
  },

  onTableChange(ctx) {
    if (!ctx) return
    this.applyDineContext(ctx)
    clientConfig.invalidateClientConfig()
    this._catsCache = null
    this._lastProductItems = null
    this._lastMenuFetchAt = 0
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
    ]).finally(() => my.stopPullDownRefresh())
  },

  storeIdForCart() {
    try {
      const ctx = dineCtx.currentDisplay()
      if (ctx && ctx.storeId) return ctx.storeId
    } catch (_) { }
    const g = appGlobal()
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
          try {
            const app = getApp()
            if (app && app.globalData) {
              app.globalData._sharedCatalog = {
                storeId: sid,
                items: catalog.items,
                categories: (catalog && catalog.categories) || categories,
                at: Date.now()
              }
            }
          } catch (_) { }
        }
      }
    } catch (_) { }

    const { list, invalidCount } = cartUtil.reconcileWithProducts(rawCart, products, categories, { complete })
    const cart = list.map((raw) => {
      const item = cartUtil.normalizeItem({ ...raw })
      return {
        ...item,
        // a:key 需要稳定唯一值：商品 ID + 排序后的规格 ID（与 cartUtil.itemKey 同源；mergeCartItem 保证同键只有一条）
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
      my.showToast({ content: '单品最多 999 份', type: 'none' })
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
    my.showToast({ content: '已清除失效商品', type: 'success' })
    this.refreshCartBar({ openSheet: !!(cart.length) })
  },

  goCheckout() {
    if (this.data.invalidCount > 0) {
      my.confirm({
        title: '有商品不可下单',
        content: `${this.data.invalidCount} 件商品已失效或库存不足，请先清除后再结算`,
        confirmButtonText: '清除失效',
        cancelButtonText: '返回',
        success: (res) => {
          if (res.confirm) this.clearInvalidCart()
        }
      })
      return
    }
    if (!this.data.cart.length || this.data.cartCount === 0) {
      my.showToast({ content: '请先点餐', type: 'none' })
      return
    }
    my.navigateTo({ url: '/packageOrder/confirm/confirm' })
  },

  scanTable() {
    dineCtx.scanAndBindTable().then((ctx) => {
      if (ctx) {
        this.applyDineContext(ctx)
        clientConfig.invalidateClientConfig()
        this._catsCache = null
        this._lastProductItems = null
        this._lastMenuFetchAt = 0
        this._menuFetchStoreId = null
        this.applyMenuFlags(ctx.storeId)
        this.loadMenu(true)
        this.refreshCartBar({ openSheet: false, forceCatalog: true })
      }
    })
  },

  async loadPrefs(force = false) {
    const g = appGlobal()
    const now = Date.now()
    const skipLogin = !force
      && g.prefsAt
      && g.prefsView
      && (now - g.prefsAt) < PREFS_SKIP_LOGIN_MS
    if (!skipLogin) {
      try {
        await auth.ensureLogin()
      } catch (_) { }
    }
    try {
      const view = await prefUtil.loadCustomerPrefs({ globalData: appGlobal() }, api, { force })
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
    const gd = appGlobal()
    const sid = (gd.table && gd.table.store_id)
      || (gd.store && gd.store.id)
      || null
    if (!sid) {
      my.showToast({ content: '请先扫桌码选择门店', type: 'none' })
      this.setData({ loading: false })
      return
    }
    const cacheKey = String(sid)
    if (this._menuInflight && this._menuInflightStoreId === cacheKey) {
      return this._menuInflight
    }
    if (
      !force
      && this._lastMenuFetchAt
      && String(this._menuFetchStoreId) === cacheKey
      && (Date.now() - this._lastMenuFetchAt) < MENU_REFRESH_TTL_MS
      && this.data.categories
      && this.data.categories.length
    ) {
      if (!this.data.recoReady) {
        this.setData({ recoReady: true, recoStoreId: sid })
      }
      return
    }

    const run = async () => {
      const now = Date.now()
      const metrics = media.layoutMetrics()
      const markFetched = (items, categories) => {
        this._lastMenuFetchAt = Date.now()
        this._menuFetchStoreId = sid
        try {
          const app = getApp()
          if (app && app.globalData && items) {
            app.globalData._sharedCatalog = {
              storeId: sid,
              items,
              categories: categories || [],
              at: this._lastMenuFetchAt
            }
          }
        } catch (_) { }
      }

      // 0) 消费 app.js 预拉取结果（冷启动提前拉取的菜单数据，跳过二次请求）
      if (!force && gd._menuPrefetch && gd._menuPrefetch.storeId === cacheKey) {
        const prefetch = gd._menuPrefetch
        gd._menuPrefetch = null
        try {
          const bundle = await prefetch.promise
          if (bundle && bundle.changed !== false && bundle.categories) {
            const catList = normalizeList(bundle.categories)
            const items = normalizeList(bundle.products)
            productApi.writeCachedMenu(sid, {
              version: bundle.version,
              categories: catList,
              products: items
            })
            this._catsCache = { key: cacheKey, at: now, cats: catList, metrics }
            this._lastProductItems = items
            const categories = this.buildCategories(catList, items, metrics, this.data.keyword)
            await this.applyCategories(categories, metrics)
            markFetched(items, catList)
            this.setData({ recoReady: true, recoStoreId: sid })
            return
          }
        } catch (_) { }
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

      // 2) 后台静默版本校验：changed:false 不重绘
      try {
        const bundle = await productApi.fetchMenuBundle(api, sid, force ? null : (cached && cached.version))
        if (bundle.changed === false) {
          if (this._catsCache) this._catsCache.at = now
          markFetched(this._lastProductItems, (this._catsCache && this._catsCache.cats) || [])
          this.setData({ recoReady: true, recoStoreId: sid })
          return
        }
        const catList = normalizeList(bundle.categories)
        const items = normalizeList(bundle.products)
        productApi.writeCachedMenu(sid, {
          version: bundle.version,
          categories: catList,
          products: items
        })
        this._catsCache = { key: cacheKey, at: now, cats: catList, metrics }
        this._lastProductItems = items
        const categories = this.buildCategories(catList, items, metrics, this.data.keyword)
        await this.applyCategories(categories, metrics)
        markFetched(items, catList)
        this.setData({ recoReady: true, recoStoreId: sid })
      } catch (e) {
        // 弱网/接口失败：已有缓存就静默保留旧菜单，无缓存才提示
        if (!cached) {
          my.showToast({ content: (e && e.message) || '加载失败', type: 'fail' })
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
    my.navigateTo({ url: `/packageOrder/product/product?id=${pid}` })
  },

  // ---- 推荐位事件（CLICK/EXPOSE 已在组件内发出）----
  onRecommendOpen(detail) {
    const pid = Number(detail && detail.id)
    if (pid) my.navigateTo({ url: `/packageOrder/product/product?id=${pid}` })
  },

  onRecommendAdd(detail) {
    const item = detail || {}
    const found = (this._lastProductItems || []).find((p) => String(p.id) === String(item.id))
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
      my.showToast({ content: check.message, type: 'none', duration: 2500 })
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
      my.showToast({ content: '已加入', type: 'success' })
      return
    }

    try {
      my.showLoading({ content: '加入中' })
      const product = await this.fetchProductDetail(id)
      if (!this.assertCanAddProduct(product)) {
        my.hideLoading()
        return
      }
      const gd = appGlobal()
      const prefs = this.data.prefs || gd.prefs || []
      const prefPrompt = prefUtil.buildQuickAddPrefPrompt(product, prefs)
      let usePrefs = false
      if (prefPrompt) {
        my.hideLoading()
        usePrefs = await askUsePrefsOnQuickAdd(prefPrompt)
        my.showLoading({ content: '加入中' })
      }
      const { selected, multi } = prefUtil.resolveQuickAddSelections(product, prefs, usePrefs)
      if (!prefUtil.selectionsComplete(product, selected, multi)) {
        my.hideLoading()
        my.navigateTo({ url: `/packageOrder/product/product?id=${id}` })
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
      my.hideLoading()
      my.showToast({ content: usePrefs ? '已按爱好加入' : '已加入', type: 'success' })
    } catch (err) {
      my.hideLoading()
      my.showToast({ content: (err && err.message) || '加入失败', type: 'fail' })
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
    const gd = appGlobal()
    const sid = (gd.table && gd.table.store_id)
      || (gd.store && gd.store.id)
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
    my.switchTab({ url: '/pages/member/member' })
  }
})
