/** 员工 WebSocket：token 走 Sec-WebSocket-Protocol，避免写入 URL 日志 */
import { getStaffToken } from '@/utils/staffAuth'

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
  return new WebSocket(url, token ? ['access_token', token] : undefined)
}
