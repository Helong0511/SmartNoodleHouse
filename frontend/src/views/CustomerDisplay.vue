<template>
  <div class="customer-display" :class="{ fullscreen }">
    <header class="header">
      <div class="brand">
        <h1>{{ storeName || '取餐叫号' }}</h1>
        <p class="subtitle">堂食看桌号 · 打包看取餐号 · 制作完成请自取</p>
      </div>
      <div class="meta">
        <span class="conn" :class="connStatus">{{ connLabel }}</span>
        <span class="clock">{{ clockText }}</span>
        <el-button v-if="storeId" size="small" type="primary" plain @click="toggleFullscreen">
          {{ fullscreen ? '退出全屏' : '全屏' }}
        </el-button>
      </div>
    </header>

    <div v-if="!storeId" class="setup">
      <h2>请配置门店</h2>
      <p>在浏览器地址栏追加参数，例如：</p>
      <code>/display?store_id=1</code>
      <p v-if="needToken" class="hint">生产环境还需 <code>&amp;token=您的叫号屏令牌</code></p>
    </div>

    <div v-else class="board">
      <section class="panel preparing">
        <div class="panel-head">
          <h2>制作中</h2>
          <span class="count">{{ preparing.length }}</span>
        </div>
        <div v-if="!preparing.length" class="empty">暂无</div>
        <ul v-else class="ticket-list">
          <li v-for="item in preparing" :key="item.id" class="ticket">
            <span class="label">{{ item.pickup_label }}</span>
            <span class="sub">{{ item.pickup_sub }}</span>
            <span class="state">{{ item.status_label }}</span>
          </li>
        </ul>
      </section>

      <section class="panel ready">
        <div class="panel-head">
          <h2>请取餐</h2>
          <span class="count highlight">{{ ready.length }}</span>
        </div>
        <p class="ready-tip">忙时可自行到取餐台领取，无需等待叫号</p>
        <div v-if="!ready.length" class="empty">请留意此区域</div>
        <ul v-else class="ticket-list ready-list">
          <li v-for="item in ready" :key="item.id" class="ticket ready-ticket">
            <span class="label">{{ item.pickup_label }}</span>
            <span class="sub">{{ item.pickup_sub }}</span>
            <span class="state pulse">请取餐</span>
          </li>
        </ul>
      </section>
    </div>

    <footer v-if="storeId" class="footer">
      <span>堂食：对照您的桌号</span>
      <span>打包/自取：对照取餐号（订单号末 4 位）</span>
    </footer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import axios from 'axios'
import { openPublicWebSocket } from '@/utils/publicWs'

type Conn = 'connecting' | 'online' | 'offline' | 'polling'
type DisplayItem = {
  id: number
  pickup_label: string
  pickup_sub: string
  pickup_code: string
  status_label: string
}

const route = useRoute()
const storeId = computed(() => {
  const raw = route.query.store_id
  const n = Number(Array.isArray(raw) ? raw[0] : raw)
  return Number.isFinite(n) && n > 0 ? n : 0
})
const displayToken = computed(() => {
  const raw = route.query.token
  return String(Array.isArray(raw) ? raw[0] : raw || '').trim()
})

const storeName = ref('')
const preparing = ref<DisplayItem[]>([])
const ready = ref<DisplayItem[]>([])
const connStatus = ref<Conn>('connecting')
const clockText = ref('')
const fullscreen = ref(false)
const needToken = ref(false)

let ws: WebSocket | null = null
let pollTimer: ReturnType<typeof setInterval> | null = null
let clockTimer: ReturnType<typeof setInterval> | null = null
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
let reconnectAttempt = 0
let knownReadyIds = new Set<number>()
let audioCtx: AudioContext | null = null

const connLabel = computed(() => {
  const m: Record<Conn, string> = {
    connecting: '连接中',
    online: '实时更新',
    offline: '已断开',
    polling: '自动刷新',
  }
  return m[connStatus.value]
})

const publicApi = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api/v1',
  timeout: 30000,
})

publicApi.interceptors.response.use((res) => {
  const data = res.data
  if (data && data.code !== 0 && data.code !== undefined) {
    return Promise.reject(new Error(data.message || '请求失败'))
  }
  return data
})

function displayParams() {
  const params: Record<string, string | number> = { store_id: storeId.value }
  if (displayToken.value) params.token = displayToken.value
  return params
}

function beepReady() {
  try {
    if (!audioCtx) audioCtx = new AudioContext()
    const ctx = audioCtx
    const osc = ctx.createOscillator()
    const gain = ctx.createGain()
    osc.connect(gain)
    gain.connect(ctx.destination)
    osc.frequency.value = 784
    gain.gain.value = 0.12
    osc.start()
    setTimeout(() => osc.stop(), 220)
  } catch {
    /* ignore */
  }
}

async function loadStoreName() {
  if (!storeId.value) return
  try {
    const res = await publicApi.get(`/miniprogram/stores/${storeId.value}`)
    storeName.value = res.data?.name || ''
  } catch {
    storeName.value = `门店 #${storeId.value}`
  }
}

async function load() {
  if (!storeId.value) return
  try {
    const res = await publicApi.get('/orders/display/active', { params: displayParams() })
    const data = res.data || {}
    const nextReady = (data.ready || []) as DisplayItem[]
    for (const item of nextReady) {
      if (!knownReadyIds.has(item.id)) {
        beepReady()
        break
      }
    }
    knownReadyIds = new Set(nextReady.map((x) => x.id))
    preparing.value = data.preparing || []
    ready.value = nextReady
    if (data.store?.name) storeName.value = data.store.name
  } catch (e: any) {
    const msg = e?.message || ''
    if (msg.includes('令牌')) needToken.value = true
  }
}

function stopPoll() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

function startPoll() {
  if (pollTimer) return
  pollTimer = setInterval(() => load(), 8000)
  connStatus.value = 'polling'
}

function scheduleReconnect() {
  if (reconnectTimer) return
  const delay = Math.min(15000, 1000 * 2 ** reconnectAttempt++)
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null
    connectWs()
  }, delay)
}

function connectWs() {
  if (!storeId.value) return
  try {
    ws?.close()
  } catch {
    /* */
  }
  connStatus.value = 'connecting'
  const params: Record<string, string | number> = { store_id: storeId.value }
  if (displayToken.value) params.token = displayToken.value
  ws = openPublicWebSocket('display', params)

  ws.onopen = () => {
    reconnectAttempt = 0
    connStatus.value = 'online'
    stopPoll()
  }
  ws.onmessage = () => load()
  ws.onerror = () => {
    connStatus.value = 'offline'
  }
  ws.onclose = () => {
    connStatus.value = 'offline'
    startPoll()
    scheduleReconnect()
  }
}

function toggleFullscreen() {
  const el = document.documentElement
  if (!document.fullscreenElement) {
    el.requestFullscreen?.().then(() => {
      fullscreen.value = true
    }).catch(() => {
      fullscreen.value = true
    })
  } else {
    document.exitFullscreen?.().then(() => {
      fullscreen.value = false
    }).catch(() => {
      fullscreen.value = false
    })
  }
}

function onFsChange() {
  fullscreen.value = !!document.fullscreenElement
}

function tickClock() {
  clockText.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
}

onMounted(async () => {
  document.title = '取餐叫号'
  tickClock()
  clockTimer = setInterval(tickClock, 1000)
  document.addEventListener('fullscreenchange', onFsChange)
  if (storeId.value) {
    await loadStoreName()
    await load()
    connectWs()
  }
})

onUnmounted(() => {
  stopPoll()
  if (clockTimer) clearInterval(clockTimer)
  if (reconnectTimer) clearTimeout(reconnectTimer)
  try {
    ws?.close()
  } catch {
    /* */
  }
  document.removeEventListener('fullscreenchange', onFsChange)
})
</script>

<style scoped>
.customer-display {
  min-height: 100vh;
  background: linear-gradient(160deg, #0f1419 0%, #1a2332 45%, #121820 100%);
  color: #eef2f7;
  padding: 20px 24px 16px;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
}
.customer-display.fullscreen { padding: 28px 32px; }

.header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 20px;
}
.brand h1 {
  margin: 0;
  font-size: clamp(28px, 4vw, 42px);
  letter-spacing: 2px;
}
.subtitle {
  margin: 8px 0 0;
  color: #9aa8bc;
  font-size: 15px;
}
.meta {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0;
}
.clock {
  font-size: 28px;
  font-variant-numeric: tabular-nums;
  font-weight: 600;
}
.conn {
  font-size: 13px;
  color: #8b98aa;
}
.conn.online { color: #52c41a; }
.conn.polling { color: #faad14; }

.setup {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  color: #b8c4d4;
}
.setup code {
  display: inline-block;
  margin: 12px 0;
  padding: 10px 16px;
  background: #243044;
  border-radius: 8px;
  color: #fff;
  font-size: 18px;
}
.hint { color: #faad14; }

.board {
  flex: 1;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px;
  min-height: 0;
}
@media (max-width: 900px) {
  .board { grid-template-columns: 1fr; }
}

.panel {
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 16px;
  padding: 20px 22px;
  display: flex;
  flex-direction: column;
  min-height: 320px;
}
.panel.preparing { border-top: 4px solid #faad14; }
.panel.ready {
  border-top: 4px solid #52c41a;
  box-shadow: 0 0 40px rgba(82, 196, 26, 0.08);
}

.panel-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 12px;
}
.panel-head h2 {
  margin: 0;
  font-size: clamp(24px, 3vw, 34px);
}
.count {
  font-size: 28px;
  font-weight: 700;
  color: #faad14;
  font-variant-numeric: tabular-nums;
}
.count.highlight { color: #52c41a; font-size: 36px; }

.ready-tip {
  margin: 0 0 16px;
  color: #7dcea0;
  font-size: 15px;
}

.empty {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #6b7a8f;
  font-size: 22px;
}

.ticket-list {
  list-style: none;
  margin: 0;
  padding: 0;
  overflow-y: auto;
  flex: 1;
}
.ticket {
  display: grid;
  grid-template-columns: 1fr auto auto;
  align-items: center;
  gap: 12px;
  padding: 14px 12px;
  border-bottom: 1px dashed rgba(255, 255, 255, 0.1);
}
.ticket:last-child { border-bottom: none; }
.label {
  font-size: clamp(28px, 4vw, 44px);
  font-weight: 700;
  letter-spacing: 1px;
}
.sub {
  font-size: 14px;
  color: #9aa8bc;
  white-space: nowrap;
}
.state {
  font-size: 16px;
  color: #faad14;
  white-space: nowrap;
}
.ready-ticket .label { color: #b7eb8f; }
.ready-ticket .state {
  color: #52c41a;
  font-weight: 700;
  font-size: 18px;
}
.pulse {
  animation: pulse 1.6s ease-in-out infinite;
}
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.55; }
}

.footer {
  margin-top: 16px;
  display: flex;
  flex-wrap: wrap;
  gap: 16px 32px;
  justify-content: center;
  color: #7a8799;
  font-size: 14px;
}
</style>
