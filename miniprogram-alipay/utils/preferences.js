/** 智能口味：加载、展示、商品页预选 */

const auth = require('./auth')

const PREFS_CACHE_MS = 60 * 1000
const MIN_ORDER_COUNT = 2

function scoreText(score) {
  const n = parseFloat(score)
  if (!Number.isFinite(n)) return ''
  return `${Math.round(n * 100)}%`
}

function formatPrefSummary(prefs, max = 3) {
  const list = prefs || []
  if (!list.length) return ''
  const text = list
    .slice(0, max)
    .map((x) => `${x.category}·${x.option_name || x.option_code}`)
    .join('、')
  return `智能口味：${text}`
}

function decoratePref(p, status) {
  const count = p.order_count || 0
  let statusText = '已确认'
  if (status === 'learning') {
    statusText = count >= MIN_ORDER_COUNT ? '学习中' : `再点${Math.max(0, MIN_ORDER_COUNT - count)}次更准`
  }
  return {
    ...p,
    key: `${p.category}::${p.option_code}`,
    status,
    statusText,
    freqText: count > 0 ? `点过${count}次` : '',
    scoreText: scoreText(p.score)
  }
}

function buildPrefDisplay(prefRes) {
  const rec = (prefRes && prefRes.preferences) || []
  const all = (prefRes && prefRes.all) || []
  const recKeys = new Set(rec.map((p) => `${p.category}::${p.option_code}`))

  const confirmed = rec.map((p) => decoratePref(p, 'confirmed'))
  const learning = all
    .filter((p) => !recKeys.has(`${p.category}::${p.option_code}`))
    .map((p) => decoratePref(p, 'learning'))

  const prefs = confirmed.length ? confirmed : learning

  return {
    confirmed,
    learning,
    prefs,
    prefSummary: formatPrefSummary(confirmed.length ? confirmed : learning),
    hasAny: confirmed.length > 0 || learning.length > 0,
    isEmpty: all.length === 0
  }
}

async function loadCustomerPrefs(app, api, options = {}) {
  const force = !!options.force
  const cid = app.globalData.customerId || auth.readStorage('customer_id')
  const token = app.globalData.customerToken || auth.readStorage('customer_token')
  if (cid) app.globalData.customerId = cid
  if (token) app.globalData.customerToken = token
  if (!cid || !token) {
    return buildPrefDisplay({})
  }
  const now = Date.now()
  if (
    !force
    && app.globalData.prefsView
    && app.globalData.prefsAt
    && now - app.globalData.prefsAt < PREFS_CACHE_MS
  ) {
    return app.globalData.prefsView
  }
  try {
    const prefRes = await api.get(`/customers/${cid}/preferences`)
    const view = buildPrefDisplay(prefRes)
    app.globalData.prefs = view.prefs
    app.globalData.prefsView = view
    app.globalData.prefsAt = now
    return view
  } catch (_) {
    return buildPrefDisplay({})
  }
}

function clearPrefsCache(app) {
  app.globalData.prefs = []
  app.globalData.prefsView = null
  app.globalData.prefsAt = 0
}

function defaultSelections(product) {
  const selected = {}
  const multi = {}
  ;(product.option_groups || []).forEach((g) => {
    if (g.selection_type === 'SINGLE' && g.options && g.options.length) {
      const normal = g.options.find((o) => o.code === 'NORMAL') || g.options[0]
      selected[g.id] = normal.id
    }
  })
  return { selected, multi }
}

function applyPrefsToProduct(product, prefs) {
  const { selected, multi } = defaultSelections(product)
  let applied = 0
  for (const g of product.option_groups || []) {
    if (g.selection_type === 'MULTIPLE') {
      const matches = (prefs || []).filter(
        (pr) => pr.category === g.name || pr.category === g.code
      )
      const ids = []
      matches.forEach((match) => {
        const opt = (g.options || []).find((o) => o.code === match.option_code)
        if (opt) ids.push(opt.id)
      })
      if (ids.length) {
        multi[g.id] = ids
        applied += ids.length
      }
      continue
    }
    const match = (prefs || []).find((pr) => pr.category === g.name || pr.category === g.code)
    if (!match) continue
    const opt = (g.options || []).find((o) => o.code === match.option_code)
    if (!opt) continue
    selected[g.id] = opt.id
    applied += 1
  }
  return {
    selected,
    multi,
    applied,
    prefHint: applied ? '已按您的智能口味爱好预选规格，可随时修改' : ''
  }
}

module.exports = {
  PREFS_CACHE_MS,
  MIN_ORDER_COUNT,
  formatPrefSummary,
  buildPrefDisplay,
  loadCustomerPrefs,
  clearPrefsCache,
  defaultSelections,
  applyPrefsToProduct
}
