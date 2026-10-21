/** 后台顾客展示名 / 昵称分栏工具。展示名≠昵称，禁止互相顶替。 */

export type CustomerNameFields = {
  customer_display_name?: string | null
  customer_nickname?: string | null
  customer_name?: string | null
  display_name?: string | null
  nickname?: string | null
}

function trimOrEmpty(v: unknown): string {
  return String(v ?? '').trim()
}

/** 展示名（员工侧），不回落昵称 */
export function customerDisplayName(row: CustomerNameFields | null | undefined): string {
  if (!row) return ''
  return trimOrEmpty(row.customer_display_name ?? row.display_name)
}

/** 昵称（渠道/小程序侧），不回落展示名 */
export function customerNickname(row: CustomerNameFields | null | undefined): string {
  if (!row) return ''
  return trimOrEmpty(row.customer_nickname ?? row.nickname)
}

/**
 * 单行主文案：有展示名用展示名；否则用昵称（兼容旧 customer_name）。
 * 需要双栏时请用 formatCustomerNameLines。
 */
export function customerPrimaryLabel(row: CustomerNameFields | null | undefined, fallback = '—'): string {
  if (!row) return fallback
  const display = customerDisplayName(row)
  if (display) return display
  const nick = customerNickname(row)
  if (nick) return nick
  const legacy = trimOrEmpty(row.customer_name)
  return legacy || fallback
}

/** 双行：主行展示名，副行仅在昵称存在且与展示名不同时显示「昵称：xxx」 */
export function formatCustomerNameLines(row: CustomerNameFields | null | undefined): {
  primary: string
  secondary: string
} {
  const display = customerDisplayName(row)
  const nick = customerNickname(row)
  if (display && nick && display !== nick) {
    return { primary: display, secondary: `昵称：${nick}` }
  }
  if (display) return { primary: display, secondary: '' }
  if (nick) return { primary: nick, secondary: '' }
  const legacy = trimOrEmpty(row?.customer_name)
  return { primary: legacy || '—', secondary: '' }
}
