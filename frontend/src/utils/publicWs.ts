// P0-2：叫号屏等公开频道 WebSocket 也需心跳保活
const HEARTBEAT_INTERVAL = 30000

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
