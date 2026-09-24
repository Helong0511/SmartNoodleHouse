/**
 * 智能推荐横滑条（Task 10，支付宝端）
 * - scene=recommend_menu：菜单页"猜你喜欢"；scene=settle_addon：结算页"顺手再来一份"
 * - 菜单首屏渲染后由页面挂载；失败/为空整体降级隐藏
 * - EXPOSE 组件实例内去重；CLICK/CART_ADD 经页面复用 quickAdd，AB 桶由 tracker 全局注入
 * - recommend_menu：30s 内存缓存 + 单飞；settle_addon：cartItemIds 变化 debounce 400ms
 */
const api = require('../../utils/api')
const media = require('../../utils/media')
const tracker = require('../../utils/tracker')

const CACHE_MS = 30 * 1000
const SETTLE_DEBOUNCE_MS = 400

/** @type {Record<string, { at: number, items: any[], ab?: any }>} */
const _menuCache = {}
/** @type {Record<string, Promise<{ items: any[], ab?: any }>>} */
const _inflight = {}

function idsKey(ids) {
  return (ids || [])
    .map((v) => Number(v))
    .filter((v) => v > 0)
    .sort((a, b) => a - b)
    .join(',')
}

function readToken() {
  try {
    const res = my.getStorageSync({ key: 'customer_token' })
    return !!(res && res.data)
  } catch (_) {
    return false
  }
}

function menuCacheKey(storeId, scene, limit) {
  return `${scene}|${storeId || 0}|${limit || 10}`
}

function reqKey(scene, storeId, limit, cartItemIds) {
  const base = menuCacheKey(storeId, scene, limit)
  if (scene === 'settle_addon') {
    return `${base}|${idsKey(cartItemIds)}`
  }
  return base
}

Component({
  props: {
    scene: 'recommend_menu',
    trackScene: 'recommend',
    storeId: null,
    cartItemIds: [],
    limit: 10,
    onOpen: null,
    onAdd: null
  },

  data: {
    loading: true,
    failed: false,
    items: [],
    title: '猜你喜欢',
    subtitle: '根据口味为你挑选'
  },

  didMount() {
    this._exposed = {}
    this._lastKey = null
    this.setData({
      title: this.props.scene === 'settle_addon' ? '顺手再来一份' : '猜你喜欢',
      subtitle: this.props.scene === 'settle_addon' ? '搭配点单更满足' : '根据口味为你挑选'
    })
    if (this.props.storeId) this.load()
  },

  didUpdate() {
    const key = [
      this.props.scene,
      this.props.storeId || 0,
      idsKey(this.props.cartItemIds)
    ].join('|')
    if (key === this._lastKey) return
    this._lastKey = key
    if (!this.props.storeId) return
    if (this.props.scene === 'settle_addon') {
      if (this._settleTimer) clearTimeout(this._settleTimer)
      this._settleTimer = setTimeout(() => {
        this._settleTimer = null
        this.load()
      }, SETTLE_DEBOUNCE_MS)
      return
    }
    this.load()
  },

  didUnmount() {
    this._exposed = {}
    if (this._settleTimer) {
      clearTimeout(this._settleTimer)
      this._settleTimer = null
    }
  },

  methods: {
    _applyResult(payload) {
      const items = (payload && payload.items) || []
      if (payload && payload.ab) tracker.setAbContext(payload.ab)
      this.setData({ items, loading: false, failed: false })
      this.reportExpose(items)
    },

    async load() {
      const sid = this.props.storeId
      if (!sid) return
      const scene = this.props.scene
      const limit = this.props.limit || 10
      const key = reqKey(scene, sid, limit, this.props.cartItemIds)

      if (scene === 'recommend_menu') {
        const cacheKey = menuCacheKey(sid, scene, limit)
        const hit = _menuCache[cacheKey]
        if (hit && Date.now() - hit.at < CACHE_MS) {
          this._applyResult(hit)
          return
        }
      }

      if (_inflight[key]) {
        this.setData({ loading: true, failed: false })
        try {
          const payload = await _inflight[key]
          this._applyResult(payload)
        } catch (_) {
          this.setData({ items: [], loading: false, failed: true })
        }
        return
      }

      this.setData({ loading: true, failed: false })
      const fetchP = (async () => {
        const params = {
          store_id: sid,
          scene,
          limit
        }
        const cartKey = idsKey(this.props.cartItemIds)
        if (scene === 'settle_addon' && cartKey) {
          params.cart_item_ids = cartKey
        }
        const res = await api.get('/miniprogram/recommendations', params, readToken())
        const rawItems = (res && res.items) || []
        const items = rawItems.map((it) => ({
          ...it,
          imageUrl: media.resolveUrl(it.main_image, '') || ''
        }))
        const payload = { items, ab: res && res.ab, at: Date.now() }
        if (scene === 'recommend_menu') {
          _menuCache[menuCacheKey(sid, scene, limit)] = payload
        }
        return payload
      })()

      _inflight[key] = fetchP.finally(() => { delete _inflight[key] })
      try {
        const payload = await _inflight[key]
        this._applyResult(payload)
      } catch (_) {
        this.setData({ items: [], loading: false, failed: true })
      }
    },

    reportExpose(items) {
      const scene = this.props.trackScene
      ;(items || []).forEach((it) => {
        if (!it || this._exposed[it.id]) return
        this._exposed[it.id] = true
        try {
          tracker.expose(it.id, {
            scene,
            category_id: it.category_id || null,
            payload: { reason: it.reason || '', badges: it.badges || [] }
          })
        } catch (_) {}
      })
    },

    onTapCard(e) {
      const id = Number(e.currentTarget.dataset.id)
      const item = (this.data.items || []).find((it) => it.id === id)
      try {
        tracker.click(id, {
          scene: this.props.trackScene,
          category_id: item ? item.category_id || null : null,
          payload: { reason: item ? item.reason || '' : '' }
        })
      } catch (_) {}
      if (typeof this.props.onOpen === 'function') this.props.onOpen({ id })
    },

    onAddTap(e) {
      const id = Number(e.currentTarget.dataset.id)
      const item = (this.data.items || []).find((it) => it.id === id)
      if (!item) return
      if (typeof this.props.onAdd === 'function') {
        this.props.onAdd({
          id: item.id,
          name: item.name,
          price: item.price,
          image: item.imageUrl,
          category_id: item.category_id || null
        })
      }
    }
  }
})
