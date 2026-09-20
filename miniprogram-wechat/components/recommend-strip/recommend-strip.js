/**
 * 智能推荐横滑条（Task 10，微信端）
 * - scene=recommend_menu：菜单页"猜你喜欢"；scene=settle_addon：结算页"顺手再来一份"
 * - 菜单首屏渲染后由页面挂载；请求失败/为空时整体降级隐藏，不打扰主流程
 * - EXPOSE 组件实例内去重；CLICK/CART_ADD 由页面复用 quickAdd 链路，埋点经 tracker 全局 AB 上下文
 */
const api = require('../../utils/api')
const media = require('../../utils/media')
const tracker = require('../../utils/tracker')

function idsKey(ids) {
  return (ids || [])
    .map((v) => Number(v))
    .filter((v) => v > 0)
    .sort((a, b) => a - b)
    .join(',')
}

function hasToken() {
  try {
    return !!wx.getStorageSync('customer_token')
  } catch (_) {
    return false
  }
}

Component({
  properties: {
    scene: { type: String, value: 'recommend_menu' },
    trackScene: { type: String, value: 'recommend' },
    storeId: { type: null, value: null },
    cartItemIds: { type: Array, value: [] },
    limit: { type: Number, value: 10 }
  },

  data: {
    loading: true,
    failed: false,
    items: [],
    title: '猜你喜欢',
    subtitle: '根据口味为你挑选'
  },

  observers: {
    'storeId, cartItemIds': function () {
      const key = [this.data.scene, this.data.storeId || 0, idsKey(this.data.cartItemIds)].join('|')
      if (key === this._lastKey) return
      this._lastKey = key
      if (this.data.storeId) this.load()
    }
  },

  lifetimes: {
    attached() {
      this._exposed = {}
      this._lastKey = null
      this.setData({
        title: this.data.scene === 'settle_addon' ? '顺手再来一份' : '猜你喜欢',
        subtitle: this.data.scene === 'settle_addon' ? '搭配点单更满足' : '根据口味为你挑选'
      })
      if (this.data.storeId) this.load()
    },
    detached() {
      this._exposed = {}
    }
  },

  methods: {
    async load() {
      const sid = this.data.storeId
      if (!sid) return
      this.setData({ loading: true, failed: false })
      try {
        const params = {
          store_id: sid,
          scene: this.data.scene,
          limit: this.data.limit || 10
        }
        const cartKey = idsKey(this.data.cartItemIds)
        if (this.data.scene === 'settle_addon' && cartKey) {
          params.cart_item_ids = cartKey
        }
        const res = await api.get('/miniprogram/recommendations', params, hasToken())
        const rawItems = (res && res.items) || []
        const items = rawItems.map((it) => ({
          ...it,
          imageUrl: media.resolveUrl(it.main_image, '') || ''
        }))
        if (res && res.ab) tracker.setAbContext(res.ab)
        this.setData({ items, loading: false, failed: false })
        this.reportExpose(items)
      } catch (_) {
        // 弱网/接口异常：静默隐藏，不影响点餐主流程
        this.setData({ items: [], loading: false, failed: true })
      }
    },

    reportExpose(items) {
      const scene = this.data.trackScene
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
          scene: this.data.trackScene,
          category_id: item ? item.category_id || null : null,
          payload: { reason: item ? item.reason || '' : '' }
        })
      } catch (_) {}
      this.triggerEvent('open', { id })
    },

    onAdd(e) {
      const id = Number(e.currentTarget.dataset.id)
      const item = (this.data.items || []).find((it) => it.id === id)
      if (!item) return
      // 加购埋点统一走页面 quickAdd 中的 cartAdd（scene=recommend/settle_addon）
      this.triggerEvent('add', {
        id: item.id,
        name: item.name,
        price: item.price,
        image: item.imageUrl,
        category_id: item.category_id || null
      })
    }
  }
})
