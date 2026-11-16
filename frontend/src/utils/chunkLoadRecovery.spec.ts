import { describe, expect, it } from 'vitest'
import { isChunkLoadError } from './chunkLoadRecovery'

describe('isChunkLoadError', () => {
  it('recognizes common browser dynamic-import failures', () => {
    expect(isChunkLoadError(new TypeError('Failed to fetch dynamically imported module'))).toBe(true)
    expect(isChunkLoadError(new Error('Loading chunk 42 failed'))).toBe(true)
    expect(isChunkLoadError(new Error('error loading dynamically imported module'))).toBe(true)
  })

  it('does not classify unrelated navigation errors as chunk failures', () => {
    expect(isChunkLoadError(new Error('Navigation cancelled'))).toBe(false)
    expect(isChunkLoadError('Network request failed')).toBe(false)
    expect(isChunkLoadError(null)).toBe(false)
  })
})
