// P0-2：WebSocket 心跳保活公共实现（员工端 ws.ts 与公开频道 publicWs.ts 共用）
// 心跳间隔 30s，小于服务端 90s 空闲超时
const HEARTBEAT_INTERVAL = 30000

/**
 * 为 WebSocket 附加心跳保活：每 30s 发送 ping，服务端收到后 touch 并回复 pong。
 * 使用 addEventListener 不覆盖组件的 onopen/onclose，兼容已有代码。
 */
export function attachHeartbeat(ws: WebSocket): void {
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
