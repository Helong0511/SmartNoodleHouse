import { describe, expect, it } from 'vitest'
import { isPrefixedCode, storePrefixFromNo } from './bizCode'

describe('bizCode', () => {
  it('storePrefixFromNo uppercases and defaults to 0000', () => {
    expect(storePrefixFromNo('sh01')).toBe('SH01')
    expect(storePrefixFromNo('')).toBe('0000')
  })

  it('isPrefixedCode accepts store-segment codes', () => {
    expect(isPrefixedCode('SH01-Mian-NiuRouMian')).toBe(true)
    expect(isPrefixedCode('bad')).toBe(false)
  })
})
