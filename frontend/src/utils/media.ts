/** 将后端相对上传路径转为可访问 URL */
export function mediaUrl(path?: string | null): string {
  if (!path) return ''
  const s = String(path).trim()
  if (!s) return ''
  if (/^https?:\/\//i.test(s)) return s
  return s.startsWith('/') ? s : `/${s}`
}

/** 按视口估算列表缩略图边长（px） */
export function thumbSizePx(windowWidth = typeof window !== 'undefined' ? window.innerWidth : 375): number {
  const w = Math.min(windowWidth, 480)
  return Math.round(Math.min(110, Math.max(72, w * 0.22)))
}
