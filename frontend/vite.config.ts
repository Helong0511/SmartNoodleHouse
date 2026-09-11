import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'node:path'

/**
 * Vite 开发服务器配置
 *
 * 【本地调试】npm run dev
 *   - host: 0.0.0.0 → 手机可通过局域网 IP 访问 H5 扫码页
 *   - /api、/uploads 代理到 127.0.0.1:8000（后端须已启动）
 *   - VITE_API_BASE_URL 在项目根 .env 中设为 /api/v1
 *
 * 【生产构建】npm run build
 *   - 不使用本 proxy；由 Nginx 将 /api 反代到 uvicorn
 *   - 静态文件托管在 frontend/dist
 */
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: { '@': resolve(import.meta.dirname, 'src') },
  },
  server: {
    // 允许手机通过 http://192.168.x.x:5173 访问（配合 .env H5_PUBLIC_BASE_URL）
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        ws: true,
      },
      '/uploads': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
})
