import { describe, expect, it } from 'vitest'
import {
  calcCouponTotalSavings,
  percentBeatsMember,
  pickBestCoupon,
  pickBestPromoPair,
  pickBestVoucher,
} from './bestPromo'

describe('bestPromo', () => {
  it('percentBeatsMember requires strictly better rate', () => {
    expect(percentBeatsMember(0.8, 0.9)).toBe(true)
    expect(percentBeatsMember(0.9, 0.9)).toBe(false)
    expect(percentBeatsMember(0.95, 0.9)).toBe(false)
  })

  it('FIXED stacks on member discount in total savings', () => {
    // 100 元 9 折会员 = 10；满减 15 → 总省 25
    const save = calcCouponTotalSavings(100, 0.9, {
      id: 1,
      coupon_type: 'FIXED',
      discount_value: '15',
      min_order_amount: '0',
    })
    expect(save).toBe(25)
  })

  it('PERCENT weaker than member rate is not usable', () => {
    const save = calcCouponTotalSavings(100, 0.9, {
      id: 2,
      coupon_type: 'PERCENT',
      discount_value: '0.95',
      min_order_amount: '0',
    })
    expect(save).toBe(0)
    expect(
      pickBestCoupon(
        [{ id: 2, coupon_type: 'PERCENT', discount_value: '0.95', min_order_amount: '0' }],
        100,
        0.9,
      ),
    ).toBeNull()
  })

  it('tie on savings prefers earlier expires_at', () => {
    const best = pickBestCoupon(
      [
        {
          id: 1,
          coupon_type: 'FIXED',
          discount_value: '10',
          min_order_amount: '0',
          expires_at: '2026-12-31T00:00:00',
        },
        {
          id: 2,
          coupon_type: 'FIXED',
          discount_value: '10',
          min_order_amount: '0',
          expires_at: '2026-11-01T00:00:00',
        },
      ],
      100,
      1,
    )
    expect(best?.id).toBe(2)
  })

  it('pickBestVoucher takes max face that payable can cover', () => {
    const best = pickBestVoucher(
      [
        { id: 1, kind: 'VOUCHER', coupon_type: 'VOUCHER', discount_value: '20', min_order_amount: '0' },
        { id: 2, kind: 'VOUCHER', coupon_type: 'VOUCHER', discount_value: '50', min_order_amount: '0' },
        { id: 3, kind: 'VOUCHER', coupon_type: 'VOUCHER', discount_value: '80', min_order_amount: '0' },
      ],
      60,
    )
    expect(best?.id).toBe(2)
  })

  it('pickBestPromoPair selects coupon and voucher together', () => {
    const { coupon, voucher } = pickBestPromoPair({
      coupons: [
        { id: 11, coupon_type: 'FIXED', discount_value: '5', min_order_amount: '0' },
        { id: 12, coupon_type: 'FIXED', discount_value: '12', min_order_amount: '0' },
      ],
      vouchers: [
        { id: 21, kind: 'VOUCHER', coupon_type: 'VOUCHER', discount_value: '10', min_order_amount: '0' },
      ],
      discountable: 100,
      memberRate: 1,
    })
    expect(coupon?.id).toBe(12)
    expect(voucher?.id).toBe(21)
  })
})
