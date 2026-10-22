/** 从 API success 包络中取出总部∪门店覆盖提示文案。 */
import { ElMessage } from 'element-plus'

type Loose = Record<string, unknown> | null | undefined

function asObj(v: unknown): Loose {
  return v && typeof v === 'object' ? (v as Record<string, unknown>) : null
}

/** 兼容 `{ data: { override_warning } }`、顶层字段，以及 `override_warnings[0]`。 */
export function pickOverrideWarning(payload: unknown): string {
  const root = asObj(payload)
  const data = asObj(root?.data) || root
  if (!data) return ''
  const single = data.override_warning
  if (typeof single === 'string' && single.trim()) return single.trim()
  const list = data.override_warnings
  if (Array.isArray(list) && list.length) {
    const first = list[0]
    if (typeof first === 'string' && first.trim()) return first.trim()
  }
  return ''
}

/** 有覆盖提示则 warning，否则 success；返回是否展示了覆盖提示。 */
export function notifySaveWithOverride(payload: unknown, successMsg = '已保存'): boolean {
  const warn = pickOverrideWarning(payload)
  if (warn) {
    ElMessage.warning(warn)
    return true
  }
  ElMessage.success(successMsg)
  return false
}
