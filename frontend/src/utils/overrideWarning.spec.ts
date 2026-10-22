import { beforeEach, describe, expect, it, vi } from 'vitest'

const { warning, success } = vi.hoisted(() => ({
  warning: vi.fn(),
  success: vi.fn(),
}))

vi.mock('element-plus', () => ({
  ElMessage: { warning, success },
}))

import { notifySaveWithOverride, pickOverrideWarning } from './overrideWarning'

describe('pickOverrideWarning', () => {
  it('reads nested data.override_warning', () => {
    expect(pickOverrideWarning({ data: { override_warning: '将覆盖总部' } })).toBe('将覆盖总部')
  })

  it('reads top-level override_warning', () => {
    expect(pickOverrideWarning({ override_warning: '提示' })).toBe('提示')
  })

  it('falls back to override_warnings[0]', () => {
    expect(
      pickOverrideWarning({ data: { override_warnings: ['第一条', '第二条'] } }),
    ).toBe('第一条')
  })

  it('returns empty when absent', () => {
    expect(pickOverrideWarning({ data: { id: 1 } })).toBe('')
    expect(pickOverrideWarning(null)).toBe('')
  })
})

describe('notifySaveWithOverride', () => {
  beforeEach(() => {
    warning.mockReset()
    success.mockReset()
  })

  it('warns when override_warning present and skips success', () => {
    const shown = notifySaveWithOverride({ data: { override_warning: '将覆盖总部同实付档' } })
    expect(shown).toBe(true)
    expect(warning).toHaveBeenCalledWith('将覆盖总部同实付档')
    expect(success).not.toHaveBeenCalled()
  })

  it('success when no override tip', () => {
    const shown = notifySaveWithOverride({ data: { id: 1 } }, '映射已保存')
    expect(shown).toBe(false)
    expect(success).toHaveBeenCalledWith('映射已保存')
    expect(warning).not.toHaveBeenCalled()
  })
})
