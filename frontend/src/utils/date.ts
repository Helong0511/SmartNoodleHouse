/** 本地日期 YYYY-MM-DD */
export function formatYmd(d: Date = new Date()): string {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

/** 当天起止（用于日期范围筛选默认值） */
export function todayDateRange(): [string, string] {
  const s = formatYmd()
  return [s, s]
}
