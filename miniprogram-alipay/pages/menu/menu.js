const api = require('../../utils/api')
const auth = require('../../utils/auth')
const cartUtil = require('../../utils/cart')
const media = require('../../utils/media')
const prefUtil = require('../../utils/preferences')
const dineCtx = require('../../utils/context')
const tabCartBadge = require('../../utils/tabCartBadge')
const tracker = require('../../utils/tracker')
const productApi = require('../../utils/products')
const clientConfig = require('../../utils/clientConfig')

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
    tableName: '',
    tableNo: '',
    seatLabel: '桌号',
    hasTable: false,
    recoReady: false,
    recoStoreId: null,
    showMenuSearch: true,
    showMenuSmartTaste: true,
    showMenuGuessYouLike: true
  },

  onLoad() {
    // onLoad 先于 onShow，提前发起菜单请求利用页面初始化时间窗口
    this._menuLoaded = false
    this.applyMenuFlags()
    this.loadMenu()
  },

  onShow() {
    tabCartBadge.syncTabCartBadge()
    this.applyMenuFlags()
    this.applyDineContext(dineCtx.currentDisplay())
    dineCtx.ensureDineContext().then((ctx) => this.applyDineContext(ctx))
    this.loadPrefs(!(appGlobal().prefsAt))
    // 首次由 onLoad 发起；后续回到页面时刷新（版本校验 changed:false 不重绘）
    if (this._menuLoaded) {
      this.loadMenu()
    }
    this._menuLoaded = true
  },

  applyMenuFlags() {
    const apply = () => {
      const flags = clientConfig.menuPageFlags()
      const patch = { ...flags }
      const hadKeyword = !!this.data.keyword
      if (!flags.showMenuSearch && hadKeyword) {
        patch.keyword = ''
      }
      this.setData(patch, () => {
        if (!flags.showMenuSearch && hadKeyword) {
          this.loadMenu()
        }
      })
    }
    apply()
    clientConfig.loadClientConfig().then(apply).catch(() => {})
  },

  applyDineContext(ctx) {
    if (!ctx) return
    this.setData(dineCtx.pageDineFields(ctx))
  },

  onTableChange(ctx) {
    if (!ctx) return
    this.applyDineContext(ctx)
    this._catsCache = null
    this.loadMenu()
  },

  onPullDownRefresh() {
    this._catsCache = null
    Promise.all([
      dineCtx.ensureDineContext({ force: true }).then((ctx) => this.applyDineContext(ctx)),
      this.loadPrefs(true),
      this.loadMenu(true)
    ]).finally(() => my.stopPullDownRefresh())
  },

  scanTable() {
    dineCtx.scanAndBindTable().then((ctx) => {
      if (ctx) {
        this.applyDineContext(ctx)
        this._catsCache = null
        this.loadMenu()
      }
    })
  },

  async loadPrefs(force = false) {
    try {
      await auth.ensureLogin()
    } catch (_) { }
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
    const catList = normalizeList(cats)
    const productList = normalizeList(items)
    return catList
      .map((c) => {
        if (!c) return null
        const products = productList
          .filter((p) => p && p.category_id === c.id)
          .filter((p) => {
            if (!kw) return true
            return (
              String(p.name || '').toLowerCase().includes(kw)
              || String(p.description || '').toLowerCase().includes(kw)
            )
          })
          .map((p) => media.decorateProduct(p, m))
        return { ...c, products }
      })
      .filter(Boolean)
      .filter((c) => c.products.length > 0 || !kw)
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
    const now = Date.now()
    const metrics = media.layoutMetrics()

    // 0) 消费 app.js 预拉取结果（冷启动提前拉取的菜单数据，跳过二次请求）
    if (!force && gd._menuPrefetch && gd._menuPrefetch.storeId === cacheKey) {
      const prefetch = gd._menuPrefetch
      gd._menuPrefetch = null
      try {
        const bundle = await prefetch.promise
        if (bundle && bundle.changed !== false && bundle.categories) {
          productApi.writeCachedMenu(sid, bundle)
          const catList = normalizeList(bundle.categories)
          const items = normalizeList(bundle.products)
          this._catsCache = { key: cacheKey, at: now, cats: catList, metrics }
          this._lastProductItems = items
          const categories = this.buildCategories(catList, items, metrics, this.data.keyword)
          await this.applyCategories(categories, metrics)
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
        this.setData({ recoReady: true, recoStoreId: sid })
        return
      }
      const catList = normalizeList(bundle.categories)
      const items = normalizeList(bundle.products)
      productApi.writeCachedMenu(sid, bundle)
      this._catsCache = { key: cacheKey, at: now, cats: catList, metrics }
      this._lastProductItems = items
      const categories = this.buildCategories(catList, items, metrics, this.data.keyword)
      await this.applyCategories(categories, metrics)
      this.setData({ recoReady: true, recoStoreId: sid })
    } catch (e) {
      // 弱网/接口失败：已有缓存就静默保留旧菜单，无缓存才提示
      if (!cached) {
        my.showToast({ content: (e && e.message) || '加载失败', type: 'fail' })
        this.setData({ loading: false })
      }
    }
  },

  async applyCategories(categories, metrics) {
    const m = metrics || media.layoutMetrics()
    const flat = []
      ; (categories || []).forEach((c) => {
        ; (c.products || []).forEach((p) => flat.push(p))
      })
    await media.hydrateImageUrls(flat, 'imageUrl')
    await media.hydrateImageUrls(flat, 'coverUrl')
    const activeCat = (categories[0] && categories[0].id) || null
    const all = categories || []

    // 分批 setData：分类超过首批阈值时，先渲染首屏可见分类，剩余延迟补全
    var firstBatch = all
    var rest = null
    if (all.length > 3) {
      firstBatch = all.slice(0, 3)
      rest = all.slice(3)
    }
    this.setData({
      categories: firstBatch,
      isWide: !!m.isWide,
      activeCat,
      scrollInto: activeCat ? `cat-${activeCat}` : '',
      loading: false
    })
    if (rest && rest.length) {
      setTimeout(() => {
        this.setData({ categories: firstBatch.concat(rest) })
      }, 0)
    }
  },

  onSearch(e) {
    const keyword = (e.detail && e.detail.value) || ''
    this.setData({ keyword })
    if (!this._catsCache || !this._lastProductItems) {
      this.loadMenu()
      return
    }
    const { cats, metrics } = this._catsCache
    const items = this._lastProductItems
    const rebuilt = this.buildCategories(cats, items, metrics, keyword)
    const activeCat = (rebuilt[0] && rebuilt[0].id) || null
    this.setData({
      categories: rebuilt,
      activeCat,
      scrollInto: activeCat ? `cat-${activeCat}` : ''
    })
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

  goMember() {
    my.switchTab({ url: '/pages/member/member' })
  }
})
