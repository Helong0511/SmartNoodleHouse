/** 智能口味：加载、展示、商品页预选 */

const auth = require('./auth')

const PREFS_CACHE_MS = 60 * 1000
const MIN_ORDER_COUNT = 2

function scoreText(score) {
  const n = parseFloat(score)
  if (!Number.isFinite(n)) return ''
  return `${Math.round(n * 100)}%`
}

function formatPrefLabel(p) {
  return `${p.category}·${p.option_name || p.option_code}`
}

function formatPrefSummary(prefs, max = 3) {
  const list = prefs || []
  if (!list.length) return ''
  const text = list.slice(0, max).map(formatPrefLabel).join('、')
  return `智能口味：${text}`
}

function formatPrefBriefBody(prefs, maxShow = 3) {
  const list = prefs || []
  if (!list.length) return ''
  const shown = list.slice(0, maxShow).map(formatPrefLabel)
  if (list.length > maxShow) return `${shown.join('、')} 等${list.length}项`
  return shown.join('、')
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

function buildPrefsForApply(all) {
  return [...(all || [])].sort((a, b) => (b.order_count || 0) - (a.order_count || 0))
}

function bestPrefForGroup(prefs, group) {
  const matches = (prefs || []).filter(
    (pr) => pr.category === group.name || pr.category === group.code
  )
  if (!matches.length) return null
  return matches.sort((a, b) => (b.order_count || 0) - (a.order_count || 0))[0]
}

function buildPrefDisplay(prefRes) {
  const rec = (prefRes && prefRes.preferences) || []
  const all = (prefRes && prefRes.all) || []
  const recKeys = new Set(rec.map((p) => `${p.category}::${p.option_code}`))

  const confirmed = rec.map((p) => decoratePref(p, 'confirmed'))
  const learning = all
    .filter((p) => !recKeys.has(`${p.category}::${p.option_code}`))
    .map((p) => decoratePref(p, 'learning'))

  const prefs = buildPrefsForApply(all)
  const displayList = prefs.length ? prefs : (confirmed.length ? confirmed : learning)

  return {
    confirmed,
    learning,
    prefs,
    prefSummary: formatPrefBriefBody(displayList),
    prefItems: displayList.map(formatPrefLabel),
    prefCount: displayList.length,
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
  if (!token) {
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
    const prefRes = await api.get('/miniprogram/customers/me/preferences')
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

function pickSelected(map, gid) {
  if (!map) return null
  if (map[gid] != null) return map[gid]
  return map[String(gid)]
}

function defaultSelections(product) {
  const selected = {}
  const multi = {}
  ;(product.option_groups || []).forEach((g) => {
    const opts = (g.options || []).filter((o) => o.status !== 0)
    if (g.selection_type === 'SINGLE' && opts.length) {
      const def = opts.find((o) => o.is_default)
        || opts.find((o) => o.code === 'NORMAL')
        || opts[0]
      selected[g.id] = def.id
    }
    if (g.selection_type === 'MULTIPLE' && opts.length) {
      const defs = opts.filter((o) => o.is_default)
      if (defs.length) multi[g.id] = defs.map((o) => o.id)
    }
  })
  return { selected, multi }
}

function selectionsComplete(product, selected, multi) {
  for (const g of product.option_groups || []) {
    if (!g.is_required) continue
    if (g.selection_type === 'SINGLE') {
      const sid = pickSelected(selected, g.id)
      if (sid == null || sid === '') return false
    } else {
      const ids = pickSelected(multi, g.id) || []
      if (!ids.length) return false
    }
  }
  return true
}

function productPrefMatches(product, prefs) {
  if (!prefs || !prefs.length) return false
  for (const g of product.option_groups || []) {
    const match = prefs.find((pr) => pr.category === g.name || pr.category === g.code)
    if (!match) continue
    const opt = (g.options || []).find((o) => o.code === match.option_code)
    if (opt) return true
  }
  return false
}

function prefLabelsForProduct(product, prefs) {
  const labels = []
  if (!product || !prefs || !prefs.length) return labels
  for (const g of product.option_groups || []) {
    const match = prefs.find((pr) => pr.category === g.name || pr.category === g.code)
    if (!match) continue
    const opt = (g.options || []).find((o) => o.code === match.option_code)
    if (opt) labels.push(`${g.name}·${opt.name}`)
  }
  return labels
}

function buildQuickAddPrefPrompt(product, prefs) {
  const labels = prefLabelsForProduct(product, prefs)
  if (!labels.length) return null
  return {
    title: '按口味爱好加购？',
    content: `检测到您的爱好：${labels.join('、')}。是否按爱好选规格？`
  }
}

function resolveQuickAddSelections(product, prefs, usePrefs) {
  if (usePrefs && prefs && prefs.length) {
    const applied = applyPrefsToProduct(product, prefs)
    return { selected: applied.selected, multi: applied.multi }
  }
  return defaultSelections(product)
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
    const match = bestPrefForGroup(prefs, g)
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
  formatPrefBriefBody,
  formatPrefLabel,
  buildPrefDisplay,
  loadCustomerPrefs,
  clearPrefsCache,
  pickSelected,
  defaultSelections,
  selectionsComplete,
  productPrefMatches,
  prefLabelsForProduct,
  buildQuickAddPrefPrompt,
  resolveQuickAddSelections,
  applyPrefsToProduct
}
