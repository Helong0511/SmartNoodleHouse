import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'node:path'
import { env } from 'node:process'
import type { IncomingMessage, ServerResponse } from 'node:http'
import type { Socket } from 'node:net'

/**
 * Vite 开发服务器配置
 *
 * 【本地调试】npm run dev
 *   - 默认仅监听 127.0.0.1；手机联调需显式设置 VITE_DEV_HOST=0.0.0.0，且不得暴露公网
 *   - /api、/uploads 代理到 127.0.0.1:8000（后端须已启动）
 *   - 后端 uvicorn --reload 重启瞬间常见 ECONNRESET：代理回 502 JSON，前端可重试
 *   - VITE_API_BASE_URL 在项目根 .env 中设为 /api
 *
 * 【生产构建】npm run build
 *   - 不使用本 proxy；由 Nginx 将 /api 反代到 uvicorn
 *   - 静态文件托管在 frontend/dist
 */

const BACKEND = 'http://127.0.0.1:8000'
// 安全默认只监听本机；手机联调时显式设置 VITE_DEV_HOST=0.0.0.0，且仅在可信局域网使用。
const DEV_HOST = env.VITE_DEV_HOST || '127.0.0.1'

const TRANSIENT_PROXY_CODES = new Set([
  'ECONNRESET',
  'ECONNREFUSED',
  'EPIPE',
  'ETIMEDOUT',
  'EHOSTUNREACH',
])

function isHttpResponse(res: unknown): res is ServerResponse {
  return !!res && typeof (res as ServerResponse).writeHead === 'function'
}

/** 后端瞬时不可达时写业务 envelope，避免浏览器只看到空白 Network Error */
function replyBackendUnavailable(res: ServerResponse | Socket | undefined, err: Error & { code?: string }) {
  if (!isHttpResponse(res) || res.writableEnded || res.headersSent) return
  const code = err.code || 'ECONNRESET'
  try {
    res.writeHead(502, { 'Content-Type': 'application/json; charset=utf-8' })
    res.end(
      JSON.stringify({
        code: 50200,
        message: '后端暂时不可用（可能正在热重载），请稍后重试',
        detail: code,
      }),
    )
  } catch {
    /* ignore */
  }
}

function attachProxyResilience(proxy: {
  on: (event: string, listener: (...args: unknown[]) => void) => void
}) {
  proxy.on('error', (err, _req, res) => {
    const e = err as Error & { code?: string }
    if (e.code && TRANSIENT_PROXY_CODES.has(e.code)) {
      replyBackendUnavailable(res as ServerResponse | Socket | undefined, e)
      return
    }
    console.error('[vite proxy]', e.message)
    replyBackendUnavailable(res as ServerResponse | Socket | undefined, e)
  })
  // WebSocket 升级失败时 Vite 仍会打日志；客户端已有指数退避重连
  proxy.on('proxyReqWs', (_proxyReq: unknown, _req: IncomingMessage, socket: Socket) => {
    socket.on('error', () => {
      /* 避免未处理的 socket error 把 Vite 进程打崩 */
    })
  })
}

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: { '@': resolve(import.meta.dirname, 'src') },
  },
  server: {
    host: DEV_HOST,
    port: 5173,
    proxy: {
      '/api': {
        target: BACKEND,
        changeOrigin: true,
        ws: true,
        timeout: 60_000,
        proxyTimeout: 60_000,
        configure: attachProxyResilience,
      },
      '/uploads': {
        target: BACKEND,
        changeOrigin: true,
        timeout: 60_000,
        proxyTimeout: 60_000,
        configure: attachProxyResilience,
      },
    },
  },
})
