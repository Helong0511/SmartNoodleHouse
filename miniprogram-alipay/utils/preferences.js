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

/** 同规格组（如加料）合并为一项，避免菜单重复显示「加料·xxx」 */
function groupPrefsByCategory(prefs) {
  const groups = []
  const map = new Map()
  for (const p of prefs || []) {
    const cat = String(p.category || '').trim()
    if (!cat) continue
    if (!map.has(cat)) {
      const group = { category: cat, options: [], items: [], key: cat }
      map.set(cat, group)
      groups.push(group)
    }
    const group = map.get(cat)
    const name = String(p.option_name || p.option_code || '').trim()
    if (name && group.options.indexOf(name) < 0) group.options.push(name)
    group.items.push(p)
  }
  return groups
}

function formatGroupedPrefLabel(group) {
  const opts = (group.options || []).join('、')
  return opts ? `${group.category}·${opts}` : group.category
}

function formatPrefSummary(prefs, max = 3) {
  const grouped = groupPrefsByCategory(prefs)
  if (!grouped.length) return ''
  const text = grouped.slice(0, max).map(formatGroupedPrefLabel).join('、')
  return `智能口味：${text}`
}

function formatPrefBriefBody(prefs, maxShow = 3) {
  const grouped = groupPrefsByCategory(prefs)
  if (!grouped.length) return ''
  const shown = grouped.slice(0, maxShow).map(formatGroupedPrefLabel)
  if (grouped.length > maxShow) return `${shown.join('、')} 等${grouped.length}项`
  return shown.join('、')
}

function groupDecoratedPrefs(items) {
  return groupPrefsByCategory(items).map((g) => {
    const status = g.items.some((i) => i.status === 'learning') ? 'learning' : 'confirmed'
    const totalCount = g.items.reduce((sum, i) => sum + (i.order_count || 0), 0)
    let statusText = '已确认'
    if (status === 'learning') {
      statusText = totalCount >= MIN_ORDER_COUNT
        ? '学习中'
        : `再点${Math.max(0, MIN_ORDER_COUNT - totalCount)}次更准`
    }
    const names = g.options.join('、')
    return {
      key: g.key,
      category: g.category,
      option_name: names,
      option_code: g.items.map((i) => i.option_code).filter(Boolean).join(','),
      status,
      statusText,
      freqText: totalCount > 0 ? `点过${totalCount}次` : '',
      scoreText: scoreText(g.items[0] && g.items[0].score)
    }
  })
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
  const groupedDisplay = groupPrefsByCategory(displayList)

  return {
    confirmed: groupDecoratedPrefs(confirmed),
    learning: groupDecoratedPrefs(learning),
    prefs,
    prefSummary: formatPrefBriefBody(displayList),
    prefItems: groupedDisplay.map(formatGroupedPrefLabel),
    prefCount: groupedDisplay.length,
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
    const store = app.globalData.store || {}
    const table = app.globalData.table || {}
    const sid = store.id || table.store_id || null
    const prefRes = await api.get('/miniprogram/customers/me/preferences', sid ? { store_id: sid } : {})
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

/** 本商品可匹配的智能口味标签（菜单 + 加购确认用；多选组如加料合并为「加料·A、B、C」） */
function prefLabelsForProduct(product, prefs) {
  const labels = []
  if (!product || !prefs || !prefs.length) return labels
  for (const g of product.option_groups || []) {
    if (g.selection_type === 'MULTIPLE') {
      const matches = (prefs || []).filter(
        (pr) => pr.category === g.name || pr.category === g.code
      )
      const names = []
      matches.forEach((match) => {
        const opt = (g.options || []).find((o) => o.code === match.option_code)
        if (opt) names.push(opt.name)
      })
      if (names.length) labels.push(`${g.name}·${names.join('、')}`)
      continue
    }
    const match = bestPrefForGroup(prefs, g)
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
  groupPrefsByCategory,
  formatGroupedPrefLabel,
  groupDecoratedPrefs,
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
