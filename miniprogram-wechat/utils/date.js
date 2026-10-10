/** 日期/时间格式化工具（本地时区，pad 补零） */

function pad2(n) {
  return n < 10 ? `0${n}` : `${n}`
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
  formatDate,
  formatDateTime,
  formatTimeLiteral
}
