/** H5 购物车规格计价（与小程序 cart.js 逻辑一致） */

function parseMoney(v: unknown): number {
  const n = parseFloat(String(v ?? ''))
  return Number.isFinite(n) ? n : 0
}

function findOption(group: any, optionId: number | string) {
  return (group.options || []).find((o: any) => String(o.id) === String(optionId))
}

export function calcUnitPrice(
  product: any,
  selected: Record<number, number>,
  multi: Record<number, number[]>,
): number {
  let total = parseMoney(product.price)
  for (const g of product.option_groups || []) {
    if (g.selection_type === 'SINGLE') {
      const sid = selected[g.id]
      const opt = sid != null ? findOption(g, sid) : null
      if (opt) total += parseMoney(opt.extra_price)
    }
    if (g.selection_type === 'MULTIPLE') {
      for (const oid of multi[g.id] || []) {
        const opt = findOption(g, oid)
        if (opt) total += parseMoney(opt.extra_price)
      }
    }
  }
  return Math.round(total * 100) / 100
}

export function buildOptionLabels(
  product: any,
  selected: Record<number, number>,
  multi: Record<number, number[]>,
): string[] {
  const labels: string[] = []
  for (const g of product.option_groups || []) {
    if (g.selection_type === 'SINGLE') {
      const sid = selected[g.id]
      const opt = sid != null ? findOption(g, sid) : null
      if (opt && opt.code !== 'NORMAL') labels.push(opt.name)
    }
    if (g.selection_type === 'MULTIPLE') {
      for (const oid of multi[g.id] || []) {
        const opt = findOption(g, oid)
        if (opt) labels.push(opt.name)
      }
    }
  }
  return labels
}

export function collectOptionIds(
  product: any,
  selected: Record<number, number>,
  multi: Record<number, number[]>,
): number[] {
  const ids: number[] = []
  for (const g of product.option_groups || []) {
    if (g.selection_type === 'SINGLE') {
      const sid = selected[g.id]
      if (sid != null && sid !== ('' as unknown as number)) ids.push(Number(sid))
    }
    if (g.selection_type === 'MULTIPLE') {
      for (const oid of multi[g.id] || []) ids.push(Number(oid))
    }
  }
  return ids.filter((id) => Number.isFinite(id) && id > 0)
}

function itemKey(item: any) {
  const ids = (item.option_ids || []).slice().sort((a: number, b: number) => a - b).join(',')
  return `${item.product_id}:${ids}`
}

export function mergeCartItem(cart: any[], entry: any) {
  const list = [...(cart || [])]
  const key = itemKey(entry)
  const idx = list.findIndex((x) => itemKey(x) === key)
  if (idx >= 0) {
    const cur = { ...list[idx] }
    const add = entry.quantity || 1
    cur.quantity = (cur.quantity || 1) + add
    cur.subtotal = (parseMoney(cur.unit_price) * cur.quantity).toFixed(2)
    list[idx] = cur
  } else {
    list.push({ ...entry })
  }
  return list
}

export function applyPreferencesToProduct(
  product: any,
  prefs: any[],
  selected: Record<number, number>,
  multi: Record<number, number[]>,
) {
  let applied = 0
  for (const g of product.option_groups || []) {
    if (g.selection_type === 'MULTIPLE') {
      const matches = (prefs || []).filter(
        (pr) => pr.category === g.name || pr.category === g.code,
      )
      const ids: number[] = []
      matches.forEach((match) => {
        const opt = (g.options || []).find((o: any) => o.code === match.option_code)
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
    const opt = (g.options || []).find((o: any) => o.code === match.option_code)
    if (!opt) continue
    selected[g.id] = opt.id
    applied += 1
  }
  return applied
}
