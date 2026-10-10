// P0-2：叫号屏等公开频道 WebSocket 也需心跳保活（公共实现在 wsHeartbeat.ts）
import { attachHeartbeat } from './wsHeartbeat'

export function openPublicWebSocket(
  channel: string,
  params?: Record<string, string | number | undefined | null>,
): WebSocket {
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
  const ws = new WebSocket(url)
  attachHeartbeat(ws)
  return ws
}
