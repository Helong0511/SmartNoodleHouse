/** 中国单位税号 / 统一社会信用代码校验（GB 32100-2015），与后端 app.utils.tax_no 对齐。 */

const USCC_CHARS = '0123456789ABCDEFGHJKLMNPQRTUWXY'
const USCC_VAL: Record<string, number> = {}
for (let i = 0; i < USCC_CHARS.length; i++) USCC_VAL[USCC_CHARS[i]] = i
const USCC_WEIGHTS = [1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28] as const
const LEGACY_TAX_RE = /^[0-9A-Z]{15,20}$/

function toHalfWidth(s: string): string {
  return s
    .replace(/[\uff01-\uff5e]/g, (ch) => String.fromCharCode(ch.charCodeAt(0) - 0xfee0))
    .replace(/\u3000/g, ' ')
}

export function normalizeTaxNo(raw: string | null | undefined): string {
  return toHalfWidth(String(raw ?? '').trim())
    .toUpperCase()
    .replace(/[^0-9A-Z]+/g, '')
}

export function isValidUscc(code: string): boolean {
  if (code.length !== 18) return false
  for (let i = 0; i < 18; i++) {
    if (USCC_VAL[code[i]] == null) return false
  }
  let total = 0
  for (let i = 0; i < 17; i++) total += USCC_VAL[code[i]] * USCC_WEIGHTS[i]
  let checkVal = 31 - (total % 31)
  if (checkVal === 31) checkVal = 0
  return code[17] === USCC_CHARS[checkVal]
}

export function taxNoErrorMessage(raw: string | null | undefined): string {
  const code = normalizeTaxNo(raw)
  if (!code) return '单位抬头必须填写税号'
  if (code.length === 18) {
    for (let i = 0; i < 18; i++) {
      if (USCC_VAL[code[i]] == null) return '税号含非法字符（不能含 I/O/Z/S/V）'
    }
    if (!isValidUscc(code)) return '税号校验失败，请核对统一社会信用代码'
    return ''
  }
  if (code.length >= 15 && code.length <= 20 && LEGACY_TAX_RE.test(code)) return ''
  return `税号格式不正确（当前 ${code.length} 位，一般为 18 位统一社会信用代码）`
}
