/** 收银/结算选优：对齐小程序 confirm 页 pickBestCoupon / pickBestVoucher 规则 */

export type PromoHold = {
  id: number
  coupon_type?: string
  kind?: string
  discount_value?: string | number
  min_order_amount?: string | number
  expires_at?: string | null
  name?: string
  summary?: string
}

export type CartPromoLine = {
  subtotal?: string | number
  quantity?: number
  no_member_discount?: boolean | number
}

function money(n: number): number {
  return Math.round(n * 100) / 100
}

export function percentBeatsMember(couponRate: unknown, memberRate: unknown): boolean {
  const cr = parseFloat(String(couponRate))
  const mr = parseFloat(String(memberRate))
  if (!Number.isFinite(cr) || cr <= 0 || cr >= 1) return false
  const m = Number.isFinite(mr) && mr > 0 && mr <= 1 ? mr : 1
  return cr < m
}

/** 可参与会员折/券比较的餐品小计（排除特价） */
export function calcDiscountableSubtotal(cart: CartPromoLine[]): number {
  let sum = 0
  for (const item of cart || []) {
    if (item.no_member_discount) continue
    sum += parseFloat(String(item.subtotal || 0)) || 0
  }
  return money(sum)
}

export function calcCouponDiscount(
  couponEligible: number,
  coupon: PromoHold | null | undefined,
  memberRate: unknown,
): number {
  if (!coupon?.id) return 0
  const eligible = Math.max(0, couponEligible)
  const minAmt = parseFloat(String(coupon.min_order_amount || 0)) || 0
  if (eligible < minAmt) return 0
  const typ = String(coupon.coupon_type || '').toUpperCase()
  if (typ === 'PERCENT') {
    const rate = parseFloat(String(coupon.discount_value))
    if (!percentBeatsMember(rate, memberRate == null ? 1 : memberRate)) return 0
    return money(eligible * (1 - rate))
  }
  const fixed = parseFloat(String(coupon.discount_value)) || 0
  return money(Math.min(fixed, eligible))
}

/** 比较总优惠：PERCENT 与会员折互斥取优；FIXED 叠在会员折后 */
export function calcCouponTotalSavings(
  discountable: number,
  memberRate: unknown,
  coupon: PromoHold | null | undefined,
): number {
  if (!coupon?.id) return 0
  const base = Math.max(0, parseFloat(String(discountable)) || 0)
  const mr = parseFloat(String(memberRate))
  const memberR = Number.isFinite(mr) && mr > 0 && mr < 1 ? mr : 1
  const memberDisc = memberR < 1 ? money(base * (1 - memberR)) : 0
  const minAmt = parseFloat(String(coupon.min_order_amount || 0)) || 0
  const typ = String(coupon.coupon_type || '').toUpperCase()
  if (typ === 'PERCENT') {
    const cr = parseFloat(String(coupon.discount_value))
    if (!percentBeatsMember(cr, memberR)) return 0
    if (base < minAmt) return 0
    return money(base * (1 - cr))
  }
  const afterMember = Math.max(0, base - memberDisc)
  if (afterMember < minAmt) return 0
  const fixed = Math.min(parseFloat(String(coupon.discount_value)) || 0, afterMember)
  return money(memberDisc + fixed)
}

export function pickBestCoupon(
  coupons: PromoHold[] | null | undefined,
  discountable: number,
  memberRate: unknown,
): PromoHold | null {
  let best: PromoHold | null = null
  let bestSave = 0
  for (const c of coupons || []) {
    if (String(c.kind || '').toUpperCase() === 'VOUCHER') continue
    if (String(c.coupon_type || '').toUpperCase() === 'VOUCHER') continue
    const save = calcCouponTotalSavings(discountable, memberRate, c)
    if (save <= 0) continue
    if (save > bestSave) {
      bestSave = save
      best = c
    } else if (save === bestSave && best) {
      const expA = c.expires_at || ''
      const expB = best.expires_at || ''
      if (expA && expB && expA < expB) best = c
    }
  }
  return best
}

export function calcVoucherDiscount(
  payable: number,
  voucher: PromoHold | null | undefined,
  eligiblePayable?: number,
): number {
  if (!voucher?.id) return 0
  const eligible = eligiblePayable != null ? eligiblePayable : payable
  const minAmt = parseFloat(String(voucher.min_order_amount || 0)) || 0
  if (eligible < minAmt) return 0
  const face = parseFloat(String(voucher.discount_value)) || 0
  if (face <= 0) return 0
  // 未达面额不使用，避免浪费（与小程序 voucher.js 一致）
  if (eligible < face) return 0
  return money(Math.min(face, eligible))
}

export function pickBestVoucher(
  vouchers: PromoHold[] | null | undefined,
  payable: number,
  eligiblePayable?: number,
): PromoHold | null {
  let best: PromoHold | null = null
  let bestDisc = 0
  const base = eligiblePayable != null ? eligiblePayable : payable
  for (const v of vouchers || []) {
    const isVoucher =
      String(v.kind || '').toUpperCase() === 'VOUCHER'
      || String(v.coupon_type || '').toUpperCase() === 'VOUCHER'
    if (!isVoucher) continue
    const disc = calcVoucherDiscount(payable, v, base)
    if (disc <= 0) continue
    if (disc > bestDisc) {
      bestDisc = disc
      best = v
    } else if (disc === bestDisc && best) {
      const expA = v.expires_at || ''
      const expB = best.expires_at || ''
      if (expA && expB && expA < expB) best = v
    }
  }
  return best
}

export function calcVoucherEligiblePayable(
  couponEligible: number,
  couponDiscount: number,
  packingFee: number,
  needPack: boolean,
): number {
  let n = Math.max(0, couponEligible - couponDiscount)
  if (needPack) n += packingFee
  return money(n)
}

/** 会员折后可用于优惠券的餐品小计 */
export function calcCouponEligibleSubtotal(discountable: number, memberDiscount: number): number {
  return money(Math.max(0, discountable - memberDiscount))
}

export function memberDiscountOf(discountable: number, memberRate: unknown): number {
  const mr = parseFloat(String(memberRate))
  if (!(Number.isFinite(mr) && mr > 0 && mr < 1)) return 0
  return money(Math.max(0, discountable) * (1 - mr))
}

/**
 * 一次性选出最优优惠券 + 代金券（可叠用）。
 * PERCENT 严格优于会员折时会员折按 1 计（由折扣券独占）。
 */
export function pickBestPromoPair(opts: {
  coupons: PromoHold[]
  vouchers: PromoHold[]
  discountable: number
  memberRate: unknown
  packingFee?: number
  needPack?: boolean
}): { coupon: PromoHold | null; voucher: PromoHold | null } {
  const discountable = Math.max(0, opts.discountable)
  const memberRate = opts.memberRate
  const packingFee = opts.packingFee || 0
  const needPack = !!opts.needPack

  const coupon = pickBestCoupon(opts.coupons, discountable, memberRate)
  let effectiveRate = parseFloat(String(memberRate))
  if (!(Number.isFinite(effectiveRate) && effectiveRate > 0 && effectiveRate <= 1)) {
    effectiveRate = 1
  }
  if (
    coupon
    && String(coupon.coupon_type || '').toUpperCase() === 'PERCENT'
    && percentBeatsMember(coupon.discount_value, effectiveRate)
  ) {
    effectiveRate = 1
  }
  const memberDisc = memberDiscountOf(discountable, effectiveRate)
  const couponEligible = calcCouponEligibleSubtotal(discountable, memberDisc)
  const couponDisc = calcCouponDiscount(couponEligible, coupon, memberRate)
  const afterMemberCoupon = money(Math.max(0, discountable - memberDisc - couponDisc))
  let pay = afterMemberCoupon
  const voucherEligible = calcVoucherEligiblePayable(
    couponEligible,
    couponDisc,
    packingFee,
    needPack,
  )
  if (needPack) pay = money(pay + packingFee)
  const voucher = pickBestVoucher(opts.vouchers, pay, voucherEligible)
  return { coupon, voucher }
}
