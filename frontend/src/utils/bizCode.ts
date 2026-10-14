/** 业务编码：与后端 option_code 一致 */
import api from '@/api'

const CODE_RE = /^[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+$/

export type CodeKind = 'product' | 'category' | 'group' | 'option'

export function storePrefixFromNo(storeNo: string | null | undefined): string {
  const raw = (storeNo || '').trim()
  return raw ? raw.toUpperCase() : '0000'
}

export function isPrefixedCode(code: string): boolean {
  return CODE_RE.test((code || '').trim())
}

export function codeHint(kind: CodeKind, prefix: string): string {
  const p = prefix || 'SH01'
  if (kind === 'option') return `选项例：${p}-Liang-XiaoWan（可只填逻辑码）`
  if (kind === 'group') return `规格组例：${p}-Liang（总部用 0000）`
  if (kind === 'category') return `分类例：${p}-MianYangMiFen`
  return `餐品例：${p}-Mian-NiuRouMian（前缀-分类-餐品）`
}

type SuggestParams = {
  name: string
  kind: CodeKind
  store_id?: number | null
  group_id?: number | null
  category_id?: number | null
  /** 餐品不选门店时用总部前缀 0000 */
  hq?: boolean
}

let suggestTimer: ReturnType<typeof setTimeout> | null = null
let suggestSeq = 0

/** 名称变化时防抖拉取默认编码（拼音首字母大写 + 门店前缀） */
export function suggestCodeFromName(
  params: SuggestParams,
  onResult: (code: string) => void,
  delay = 280,
) {
  if (suggestTimer) clearTimeout(suggestTimer)
  const seq = ++suggestSeq
  const name = (params.name || '').trim()
  if (!name) return
  suggestTimer = setTimeout(async () => {
    try {
      const res = await api.get('/menu/code-suggest', {
        params: {
          name,
          kind: params.kind,
          ...(params.store_id != null ? { store_id: params.store_id } : {}),
          ...(params.group_id != null ? { group_id: params.group_id } : {}),
          ...(params.category_id != null ? { category_id: params.category_id } : {}),
          ...(params.hq ? { hq: true } : {}),
        },
      })
      if (seq !== suggestSeq) return
      const code = res.data?.code
      if (code) onResult(String(code))
    } catch {
      /* 忽略建议失败，用户可手填 */
    }
  }, delay)
}
