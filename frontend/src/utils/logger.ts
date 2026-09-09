/** 前端 / H5 全部日志 → POST /api/v1/client-logs → logs/frontend/ */
const FLUSH_MS = 5000
const MAX_BATCH = 50
const MAX_QUEUE = 500

type Level = 'DEBUG' | 'INFO' | 'WARN' | 'ERROR'

interface LogEntry {
  level: Level
  message: string
  ts: string
  page?: string
  extra?: unknown
}

const queue: LogEntry[] = []
let flushTimer: ReturnType<typeof setTimeout> | null = null
let flushing = false
let installed = false
let reporting = false // 上报过程中禁止再入队，避免环路

const originalConsole: Partial<Record<string, (...args: unknown[]) => void>> = {}

function apiBase(): string {
  return (import.meta.env.VITE_API_BASE_URL as string) || '/api/v1'
}

function stringifyArg(v: unknown): string {
  if (v == null) return String(v)
  if (typeof v === 'string') return v
  if (v instanceof Error) return `${v.name}: ${v.message}`
  try {
    return JSON.stringify(v)
  } catch {
    return String(v)
  }
}

function push(level: Level, message: string, extra?: unknown) {
  if (reporting) return
  const msg = String(message || '').slice(0, 4000)
  if (!msg) return
  queue.push({
    level,
    message: msg,
    ts: new Date().toISOString(),
    page: typeof location !== 'undefined' ? location.pathname : undefined,
    extra: extra === undefined ? undefined : safeExtra(extra),
  })
  while (queue.length > MAX_QUEUE) queue.shift()
  scheduleFlush()
}

function safeExtra(v: unknown): unknown {
  try {
    if (v instanceof Error) return { name: v.name, message: v.message, stack: String(v.stack || '').slice(0, 1500) }
    return JSON.parse(JSON.stringify(v))
  } catch {
    return String(v).slice(0, 500)
  }
}

function scheduleFlush() {
  if (flushTimer) return
  flushTimer = setTimeout(() => {
    flushTimer = null
    void flush()
  }, FLUSH_MS)
  if (queue.length >= MAX_BATCH) {
    if (flushTimer) clearTimeout(flushTimer)
    flushTimer = null
    void flush()
  }
}

async function flush() {
  if (flushing || !queue.length) return
  flushing = true
  const batch = queue.splice(0, MAX_BATCH)
  reporting = true
  try {
    await fetch(`${apiBase()}/client-logs`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        source: 'frontend',
        app_version: (import.meta.env.VITE_APP_VERSION as string) || undefined,
        device: typeof navigator !== 'undefined' ? navigator.userAgent.slice(0, 120) : undefined,
        entries: batch,
      }),
      keepalive: true,
    })
  } catch {
    queue.unshift(...batch)
    while (queue.length > MAX_QUEUE) queue.pop()
  } finally {
    reporting = false
    flushing = false
    if (queue.length) scheduleFlush()
  }
}

function hookConsole() {
  const map: Record<string, Level> = {
    debug: 'DEBUG',
    log: 'INFO',
    info: 'INFO',
    warn: 'WARN',
    error: 'ERROR',
  }
  ;(Object.keys(map) as (keyof typeof map)[]).forEach((key) => {
    const orig = console[key as keyof Console]
    if (typeof orig !== 'function') return
    const origFn = orig as (...args: unknown[]) => void
    originalConsole[key] = origFn.bind(console)
    ;(console as any)[key] = (...args: unknown[]) => {
      try {
        push(map[key], args.map(stringifyArg).join(' '))
      } catch {
        /* ignore */
      }
      return (originalConsole[key] as (...a: unknown[]) => void)(...args)
    }
  })
}

/** 供 axios 等调用：上报 API 失败（跳过 client-logs 自身） */
export function reportApiLog(
  level: Level,
  method: string,
  url: string,
  detail?: unknown,
) {
  const path = String(url || '')
  if (path.includes('/client-logs')) return
  push(level, `api ${method.toUpperCase()} ${path}`, detail)
}

export const logger = {
  debug: (m: string, extra?: unknown) => push('DEBUG', m, extra),
  info: (m: string, extra?: unknown) => push('INFO', m, extra),
  warn: (m: string, extra?: unknown) => push('WARN', m, extra),
  error: (m: string, extra?: unknown) => push('ERROR', m, extra),
  flush,
}

/** 劫持 console + 全局错误，批量上报后端；幂等 */
export function installFrontendLogger() {
  if (installed || typeof window === 'undefined') return
  installed = true
  hookConsole()
  window.addEventListener('error', (ev) => {
    push('ERROR', ev.message || 'window.error', {
      filename: ev.filename,
      lineno: ev.lineno,
      colno: ev.colno,
    })
  })
  window.addEventListener('unhandledrejection', (ev) => {
    const reason = (ev as PromiseRejectionEvent).reason
    push('ERROR', 'unhandledrejection', reason)
  })
  window.addEventListener('pagehide', () => {
    void flush()
  })
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') void flush()
  })
}

export default logger
