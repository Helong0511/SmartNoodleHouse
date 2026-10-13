/** 日期/时间格式化工具（本地时区，pad 补零） */

function pad2(n) {
  return n < 10 ? `0${n}` : `${n}`
}

/** 小程序 date picker 返回值 → YYYY-MM-DD；无法解析返回 '' */
function normalizePickerDate(raw) {
  const s = String(raw || '').trim()
  if (!s) return ''
  const dash = s.match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/)
  if (dash) return `${dash[1]}-${pad2(+dash[2])}-${pad2(+dash[3])}`
  const slash = s.match(/^(\d{4})\/(\d{1,2})\/(\d{1,2})$/)
  if (slash) return `${slash[1]}-${pad2(+slash[2])}-${pad2(+slash[3])}`
  if (/^\d{4}-\d{2}-\d{2}$/.test(s)) return s
  const viaFormat = formatDate(s)
  return /^\d{4}-\d{2}-\d{2}$/.test(viaFormat) ? viaFormat : ''
}

/** ISO 字符串/Date → YYYY-MM-DD；空值返回 ''，无效值回退原文前 10 位 */
function formatDate(value) {
  if (!value) return ''
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return String(value).slice(0, 10)
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`
}

/** ISO 字符串 → YYYY-MM-DD HH:mm；空值返回 ''，无效值回退原文前 16 位（T→空格） */
function formatDateTime(value) {
  if (!value) return ''
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return String(value).slice(0, 16).replace('T', ' ')
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`
}

/** 字面量格式化（不解析时区）：正则取 YYYY-MM-DD HH:mm，否则原文 T→空格后截断 16 位 */
function formatTimeLiteral(value) {
  const m = String(value || '').match(/(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/)
  return m ? `${m[1]} ${m[2]}` : String(value || '').replace('T', ' ').slice(0, 16)
}

module.exports = {
  pad2,
  normalizePickerDate,
  formatDate,
  formatDateTime,
  formatTimeLiteral
}
