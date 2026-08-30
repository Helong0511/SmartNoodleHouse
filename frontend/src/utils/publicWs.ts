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
  const url = `${proto}://${location.host}/api/v1/ws/${channel}${query ? `?${query}` : ''}`
  return new WebSocket(url)
}
