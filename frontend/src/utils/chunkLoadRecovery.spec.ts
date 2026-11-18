import { describe, expect, it, vi } from 'vitest'
import {
  isChunkLoadError,
  readSessionFlag,
  removeSessionFlag,
  writeSessionFlag,
} from './chunkLoadRecovery'

describe('isChunkLoadError', () => {
  it('recognizes common browser dynamic-import failures', () => {
    expect(isChunkLoadError(new TypeError('Failed to fetch dynamically imported module'))).toBe(true)
    expect(isChunkLoadError(new Error('Loading chunk 42 failed'))).toBe(true)
    expect(isChunkLoadError(new Error('error loading dynamically imported module'))).toBe(true)
    expect(isChunkLoadError(new TypeError('Importing a module script failed.'))).toBe(true)
  })

  it('does not classify unrelated navigation errors as chunk failures', () => {
    expect(isChunkLoadError(new Error('Navigation cancelled'))).toBe(false)
    expect(isChunkLoadError('Network request failed')).toBe(false)
    expect(isChunkLoadError(null)).toBe(false)
  })
})

describe('session storage recovery helpers', () => {
  it('returns safe defaults when session storage access throws', () => {
    const throwingStorage = {
      getItem: () => { throw new DOMException('Blocked', 'SecurityError') },
      setItem: () => { throw new DOMException('Blocked', 'SecurityError') },
      removeItem: () => { throw new DOMException('Blocked', 'SecurityError') },
    }
    vi.stubGlobal('window', { sessionStorage: throwingStorage })

    try {
      expect(readSessionFlag('retry')).toBeNull()
      expect(writeSessionFlag('retry', '1')).toBe(false)
      expect(() => removeSessionFlag('retry')).not.toThrow()
    } finally {
      vi.unstubAllGlobals()
    }
  })

  it('reads, writes, and removes session flags when storage is available', () => {
    const values = new Map<string, string>()
    vi.stubGlobal('window', {
      sessionStorage: {
        getItem: (key: string) => values.get(key) ?? null,
        setItem: (key: string, value: string) => values.set(key, value),
        removeItem: (key: string) => values.delete(key),
      },
    })

    try {
      expect(writeSessionFlag('retry', '1')).toBe(true)
      expect(readSessionFlag('retry')).toBe('1')
      removeSessionFlag('retry')
      expect(readSessionFlag('retry')).toBeNull()
    } finally {
      vi.unstubAllGlobals()
    }
  })
})
