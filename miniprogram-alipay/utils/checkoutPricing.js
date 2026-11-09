/** 结算页计价/选券/公益兜底：纯函数，供 confirm 复用。 */
const { pad2, formatDate } = require('./date')

/** 今天/明天两档，30 分钟粒度（11:00–20:30），仅保留 ≥ 当前时间+30 分钟的槽位 */
function buildPickupSlots(dayIdx) {
  const base = new Date()
  base.setDate(base.getDate() + (dayIdx === 1 ? 1 : 0))
  const dateStr = formatDate(base)
  const minTime = Date.now() + 30 * 60 * 1000
  const slots = []
  for (let h = 11; h <= 20; h++) {
    for (const m of [0, 30]) {
      const label = `${pad2(h)}:${pad2(m)}`
      const dt = new Date(base.getFullYear(), base.getMonth(), base.getDate(), h, m, 0)
      if (dt.getTime() < minTime) continue
      slots.push({ label, iso: `${dateStr}T${label}:00` })
    }
  }
  return slots
}

function memberDiscountText(rate) {
  const n = parseFloat(rate)
  if (!Number.isFinite(n) || n >= 1) return ''
  const zhe = Math.round(n * 100) / 10
  return `${zhe}折`
}

/** 本地兜底费率（无 preview 时用；正式以 /social-benefits/preview 为准） */
const SOCIAL_RATE_HINT = {
  SANITATION: 0.8,
  RIDER: 0.8,
  ELDER: 0.85,
  TEACHER: 0.9,
  STUDENT: 0.9,
  MEDICAL: 0.85,
  DISABLED: 0.85,
  VETERAN: 0.85,
  VOLUNTEER: 0.9
}
const SOCIAL_LABEL = {
  SANITATION: '环卫工公益',
  RIDER: '骑手公益',
  ELDER: '长者公益',
  TEACHER: '教师公益',
  STUDENT: '学生公益',
  MEDICAL: '医护公益',
  DISABLED: '残障公益',
  VETERAN: '退役军人公益',
  VOLUNTEER: '志愿者公益'
}

function bestSocialHint(codes) {
  let bestCode = ''
  let bestRate = 1
  for (const c of codes || []) {
    const r = SOCIAL_RATE_HINT[c]
    if (Number.isFinite(r) && r < bestRate) {
      bestRate = r
      bestCode = c
    }
  }
  if (!bestCode) return null
  return { code: bestCode, rate: bestRate, label: SOCIAL_LABEL[bestCode] || '公益优惠', fromPreview: false }
}

function socialSubsidyOf(hint, discountable) {
  if (!hint) return 0
  const s = parseFloat(hint.subsidy)
  if (Number.isFinite(s) && s >= 0) return Math.round(s * 100) / 100
  const r = parseFloat(hint.rate)
  if (!(r > 0 && r < 1)) return 0
  const base = Math.max(0, parseFloat(discountable) || 0)
  return Math.round(base * (1 - r) * 100) / 100
}

function memberOnlySave(discountable, memberRate) {
  const base = Math.max(0, parseFloat(discountable) || 0)
  const mr = parseFloat(memberRate)
  if (!(Number.isFinite(mr) && mr > 0 && mr < 1)) return 0
  return Math.round(base * (1 - mr) * 100) / 100
}

function pickBestCoupon(coupons, discountable, memberRate, calcTotalSavingsFn) {
  // 按「总优惠力度」取最优：FIXED=会员折+满减；PERCENT=仅当严格优于会员折时独占折扣
  let best = null
  let bestSave = 0
  for (const c of coupons || []) {
    const save = calcTotalSavingsFn(discountable, memberRate, c)
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

function percentBeatsMember(couponRate, memberRate) {
  const cr = parseFloat(couponRate)
  const mr = parseFloat(memberRate)
  if (!Number.isFinite(cr) || cr <= 0 || cr >= 1) return false
  const m = Number.isFinite(mr) && mr > 0 && mr <= 1 ? mr : 1
  return cr < m
}

function pickBestVoucher(vouchers, payable, calcFn) {
  let best = null
  let bestDisc = 0
  for (const v of vouchers || []) {
    const disc = calcFn(payable, v)
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

function calcCouponDiscount(couponEligible, coupon, memberRate) {
  if (!coupon || !coupon.id) return 0
  const rawEligible = Number(couponEligible)
  const eligible = Number.isFinite(rawEligible) ? Math.max(0, rawEligible) : 0
  const rawMin = Number(coupon.min_order_amount || 0)
  const minAmt = Number.isFinite(rawMin) ? Math.max(0, rawMin) : 0
  if (eligible < minAmt) return 0
  if (coupon.coupon_type === 'PERCENT') {
    const rate = parseFloat(coupon.discount_value)
    if (!percentBeatsMember(rate, memberRate == null ? 1 : memberRate)) return 0
    return Math.max(0, Math.round(eligible * (1 - rate) * 100) / 100)
  }
  const rawFixed = Number(coupon.discount_value)
  const fixed = Number.isFinite(rawFixed) ? Math.max(0, rawFixed) : 0
  return Math.max(0, Math.min(fixed, eligible))
}

/** 比较总优惠：PERCENT 与会员折互斥取优；FIXED 叠在会员折后 */
function calcCouponTotalSavings(discountable, memberRate, coupon) {
  if (!coupon || !coupon.id) return 0
  const base = Math.max(0, parseFloat(discountable) || 0)
  const mr = parseFloat(memberRate)
  const memberR = Number.isFinite(mr) && mr > 0 && mr < 1 ? mr : 1
  const memberDisc = memberR < 1 ? Math.round(base * (1 - memberR) * 100) / 100 : 0
  const rawMin = Number(coupon.min_order_amount || 0)
  const minAmt = Number.isFinite(rawMin) ? Math.max(0, rawMin) : 0
  if (coupon.coupon_type === 'PERCENT') {
    const cr = parseFloat(coupon.discount_value)
    if (!percentBeatsMember(cr, memberR)) return 0
    if (base < minAmt) return 0
    return Math.round(base * (1 - cr) * 100) / 100
  }
  const afterMember = Math.max(0, base - memberDisc)
  if (afterMember < minAmt) return 0
  const rawFixed = Number(coupon.discount_value)
  const fixed = Math.min(Number.isFinite(rawFixed) ? Math.max(0, rawFixed) : 0, afterMember)
  return Math.max(0, Math.round((memberDisc + fixed) * 100) / 100)
}

module.exports = {
  SOCIAL_LABEL,
  SOCIAL_RATE_HINT,
  bestSocialHint,
  buildPickupSlots,
  calcCouponDiscount,
  calcCouponTotalSavings,
  memberDiscountText,
  memberOnlySave,
  percentBeatsMember,
  pickBestCoupon,
  pickBestVoucher,
  socialSubsidyOf
}
