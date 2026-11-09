import { describe, expect, it } from 'vitest'
import { cartItemKey, parseStoredCart } from './cart'

describe('parseStoredCart', () => {
  it('returns an empty cart for malformed JSON and non-array payloads', () => {
    expect(parseStoredCart('{broken')).toEqual([])
    expect(parseStoredCart('{"product_id":1}')).toEqual([])
    expect(parseStoredCart(null)).toEqual([])
  })

  it('keeps only valid positive-quantity product rows', () => {
    expect(parseStoredCart(JSON.stringify([
      { product_id: 1, quantity: 2 },
      { product_id: 2, quantity: -1 },
      { product_id: 0, quantity: 1 },
      null,
    ]))).toEqual([{ product_id: 1, quantity: 2 }])
  })

  it('creates stable keys from product and sorted options', () => {
    expect(cartItemKey({ product_id: 7, option_ids: [3, 1] }, 0))
      .toBe(cartItemKey({ product_id: 7, option_ids: [1, 3] }, 0))
  })
})
