/** 员工 WebSocket：token 走 Sec-WebSocket-Protocol，避免写入 URL 日志 */
import { getStaffToken } from '@/utils/staffAuth'

// P0-2：心跳间隔 30s，小于服务端 90s 空闲超时
const HEARTBEAT_INTERVAL = 30000

/**
 * 为 WebSocket 附加心跳保活：每 30s 发送 ping，服务端收到后 touch 并回复 pong。
 * 使用 addEventListener 不覆盖组件的 onopen/onclose，兼容已有代码。
 */
function attachHeartbeat(ws: WebSocket): void {
  let timer: ReturnType<typeof setInterval> | null = null
  ws.addEventListener('open', () => {
    timer = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) {
        try {
          ws.send(JSON.stringify({ type: 'ping' }))
        } catch {
          /* ignore */
        }
      }
    }, HEARTBEAT_INTERVAL)
  })
  const clear = () => {
    if (timer) {
      clearInterval(timer)
      timer = null
    }
  }
  ws.addEventListener('close', clear)
  ws.addEventListener('error', clear)
}

export function openStaffWebSocket(
  channel: string,
  params?: Record<string, string | number | undefined | null>
): WebSocket {
  const token = getStaffToken()
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const qs = new URLSearchParams()
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null && String(v) !== '') {
        qs.set(k, String(v))
      }
    }
  }
  const query = qs.toString()
  const url = `${proto}://${location.host}/api/ws/${channel}${query ? `?${query}` : ''}`
  const ws = new WebSocket(url, token ? ['access_token', token] : undefined)
  attachHeartbeat(ws)
  return ws
}
