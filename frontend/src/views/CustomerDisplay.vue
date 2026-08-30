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
        <VoiceSwitch v-model="soundOn" />
        <el-button v-if="storeId" size="small" type="warning" plain @click="testVoice">试听语音</el-button>
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

    <div v-if="storeId && soundOn && !voiceUnlocked" class="voice-unlock" @click.stop="enableVoice">
      点击此处或点击屏幕任意位置，启用取餐语音播报
    </div>

    <div v-if="storeId" class="board">
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

      <section class="panel ready" :class="{ 'has-overdue': hasOverdueReady }">
        <div class="panel-head">
          <h2>请取餐</h2>
          <span class="count highlight">{{ ready.length }}</span>
        </div>
        <p class="ready-tip">忙时可自行到取餐台领取，无需等待叫号</p>
        <p v-if="hasOverdueReady" class="overdue-banner">以下餐品等待较久，请尽快取餐</p>
        <div v-if="!ready.length" class="empty">请留意此区域</div>
        <ul v-else class="ticket-list ready-list">
          <li
            v-for="item in ready"
            :key="item.id"
            class="ticket ready-ticket"
            :class="{ overdue: isReadyOverdue(item.id) }"
          >
            <span class="label">{{ item.pickup_label }}</span>
            <span class="sub">{{ item.pickup_sub }}</span>
            <span class="state" :class="isReadyOverdue(item.id) ? 'overdue-flash' : 'pulse'">
              {{ isReadyOverdue(item.id) ? '请尽快取餐' : '请取餐' }}
            </span>
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
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import axios from 'axios'
import { ElMessage } from 'element-plus'
import VoiceSwitch from '@/components/VoiceSwitch.vue'
import { openPublicWebSocket } from '@/utils/publicWs'
import {
  announcePickupOverdue,
  announcePickupReady,
  bindAutoVoiceUnlock,
  isVoiceUnlocked,
  readVoiceSoundOn,
  stopAllVoice,
  unlockVoice,
  VOICE_SOUND_KEYS,
  writeVoiceSoundOn,
} from '@/utils/voiceAlert'

type Conn = 'connecting' | 'online' | 'offline' | 'polling'
type DisplayItem = {
  id: number
  pickup_label: string
  pickup_sub: string
  pickup_code: string
  status_label: string
}

/** 首次进入请取餐后的重复播报间隔（毫秒）：2 分 → 3 分 → 5 分 → 5 分 → 10 分 */
const REMIND_INTERVALS_MS = [120_000, 180_000, 300_000, 300_000, 600_000]
const MAX_REMIND_PER_ORDER = 6
const REMIND_TICK_MS = 30_000
/** 超过此时长仍待取餐：屏幕闪烁高亮 + 超时语音 */
const OVERDUE_READY_MS = 5 * 60_000
/** 超时后重复语音间隔 */
const OVERDUE_REMIND_MS = 3 * 60_000

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
const soundOn = ref(readVoiceSoundOn(VOICE_SOUND_KEYS.display, true))

watch(soundOn, (on) => {
  writeVoiceSoundOn(VOICE_SOUND_KEYS.display, on)
  if (!on) stopAllVoice()
})
const voiceUnlocked = ref(isVoiceUnlocked())

let ws: WebSocket | null = null
let pollTimer: ReturnType<typeof setInterval> | null = null
let clockTimer: ReturnType<typeof setInterval> | null = null
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
let reconnectAttempt = 0
let displaySnapshotReady = false
/** 待取餐语音：count=已播次数，lastAt=上次播报时间，firstAt=首次进入请取餐区，overdue=已触发超时提醒 */
const readyVoiceTrack = new Map<number, { count: number; lastAt: number; firstAt: number; overdue: boolean }>()
const overdueTimers = new Map<number, ReturnType<typeof setTimeout>>()
let remindTimer: ReturnType<typeof setInterval> | null = null
/** 驱动超时高亮每秒刷新 */
const overdueTick = ref(0)

async function enableVoice() {
  voiceUnlocked.value = await unlockVoice('取餐语音已开启')
  if (voiceUnlocked.value) {
    ElMessage.success('取餐语音已开启')
  } else {
    ElMessage.warning('无法播放，请检查浏览器与系统音量')
  }
}

async function testVoice() {
  if (!soundOn.value) {
    ElMessage.info('请先打开「语音提示」开关')
    return
  }
  voiceUnlocked.value = await unlockVoice('取餐语音已开启')
  if (!voiceUnlocked.value) {
    ElMessage.warning('无法播放，请检查浏览器与系统音量')
    return
  }
  setTimeout(() => {
    announcePickupReady('8号桌', true)
  }, 700)
}

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

function clearOverdueTimer(id: number) {
  const t = overdueTimers.get(id)
  if (t) {
    clearTimeout(t)
    overdueTimers.delete(id)
  }
}

function syncReadyTrack(items: DisplayItem[]) {
  const ids = new Set(items.map((x) => x.id))
  for (const id of readyVoiceTrack.keys()) {
    if (!ids.has(id)) {
      readyVoiceTrack.delete(id)
      clearOverdueTimer(id)
    }
  }
  const now = Date.now()
  for (const item of items) {
    if (!readyVoiceTrack.has(item.id)) {
      readyVoiceTrack.set(item.id, { count: 0, lastAt: 0, firstAt: now, overdue: false })
      scheduleOverdueAlert(item)
    }
  }
}

function scheduleOverdueAlert(item: DisplayItem) {
  clearOverdueTimer(item.id)
  const track = readyVoiceTrack.get(item.id)
  if (!track || track.overdue) return
  const delay = Math.max(0, OVERDUE_READY_MS - (Date.now() - track.firstAt))
  overdueTimers.set(item.id, setTimeout(() => {
    overdueTimers.delete(item.id)
    if (!ready.value.some((x) => x.id === item.id)) return
    triggerOverdueAlert(item)
  }, delay))
}

function isReadyOverdue(id: number) {
  void overdueTick.value
  const track = readyVoiceTrack.get(id)
  if (!track) return false
  return track.overdue || Date.now() - track.firstAt >= OVERDUE_READY_MS
}

const hasOverdueReady = computed(() => ready.value.some((item) => isReadyOverdue(item.id)))

function markVoicePlayed(item: DisplayItem, track: { count: number; lastAt: number; firstAt: number; overdue: boolean }) {
  const now = Date.now()
  track.count += 1
  track.lastAt = now
  if (!track.firstAt) track.firstAt = now
  readyVoiceTrack.set(item.id, track)
}

function announceReadyItem(item: DisplayItem, overdue = false) {
  if (!soundOn.value || !isVoiceUnlocked()) return false
  const ok = overdue
    ? announcePickupOverdue(item.pickup_label, true)
    : announcePickupReady(item.pickup_label, true)
  if (!ok) return false
  const now = Date.now()
  const track = readyVoiceTrack.get(item.id) || { count: 0, lastAt: 0, firstAt: now, overdue: false }
  markVoicePlayed(item, track)
  return true
}

function triggerOverdueAlert(item: DisplayItem) {
  const track = readyVoiceTrack.get(item.id)
  if (!track || track.overdue) return
  track.overdue = true
  readyVoiceTrack.set(item.id, track)
  overdueTick.value += 1
  announceReadyItem(item, true)
}

function ensureOverdueAlerts() {
  for (const item of ready.value) {
    const track = readyVoiceTrack.get(item.id)
    if (!track || track.overdue) continue
    if (Date.now() - track.firstAt >= OVERDUE_READY_MS) {
      triggerOverdueAlert(item)
      return
    }
  }
}

/** 新进入请取餐的单：首次播报；久等未取：按间隔重复播报（每次只播一条，避免叠音） */
function processReadyVoice(items: DisplayItem[], allowNewAlert: boolean) {
  if (!soundOn.value) return
  syncReadyTrack(items)
  const now = Date.now()

  if (allowNewAlert) {
    for (const item of items) {
      const track = readyVoiceTrack.get(item.id)!
      if (track.count === 0) {
        announceReadyItem(item)
        return
      }
    }
  } else {
    // 首屏加载：已有待取餐单只标记，不播报（避免打开/刷新叫号屏时连播）
    for (const item of items) {
      const track = readyVoiceTrack.get(item.id)!
      if (track.count === 0) {
        track.count = 1
        track.lastAt = now
      }
    }
  }

  let pick: DisplayItem | null = null
  let pickElapsed = 0
  let pickOverdue = false
  for (const item of items) {
    const track = readyVoiceTrack.get(item.id)!
    if (track.count >= MAX_REMIND_PER_ORDER || track.count < 1) continue
    const overdue = track.overdue
    const interval = overdue
      ? OVERDUE_REMIND_MS
      : REMIND_INTERVALS_MS[Math.min(track.count - 1, REMIND_INTERVALS_MS.length - 1)]
    const elapsed = now - track.lastAt
    if (elapsed >= interval && elapsed > pickElapsed) {
      pick = item
      pickElapsed = elapsed
      pickOverdue = overdue
    }
  }
  if (pick) announceReadyItem(pick, pickOverdue)
}

function tickReadyRemind() {
  if (!storeId.value || !ready.value.length) return
  ensureOverdueAlerts()
  processReadyVoice(ready.value, false)
}

async function load() {
  if (!storeId.value) return
  try {
    const res = await publicApi.get('/orders/display/active', { params: displayParams() })
    const data = res.data || {}
    const nextReady = (data.ready || []) as DisplayItem[]
    processReadyVoice(nextReady, displaySnapshotReady)
    displaySnapshotReady = true
    preparing.value = data.preparing || []
    ready.value = nextReady
    if (data.store?.name) storeName.value = data.store.name
  } catch (e: any) {
    const msg = e?.message || ''
    if (msg.includes('令牌')) needToken.value = true
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
  const goFull = !document.fullscreenElement
  if (goFull && soundOn.value && !voiceUnlocked.value) {
    unlockVoice('取餐语音已开启').then((ok) => {
      voiceUnlocked.value = ok
    })
  }
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
  if (ready.value.length) overdueTick.value += 1
}

let unbindVoice: (() => void) | null = null

onMounted(async () => {
  unbindVoice = bindAutoVoiceUnlock((ok) => {
    if (ok) voiceUnlocked.value = true
  })

  document.title = '取餐叫号'
  tickClock()
  clockTimer = setInterval(tickClock, 1000)
  document.addEventListener('fullscreenchange', onFsChange)
  if (storeId.value) {
    await loadStoreName()
    await load()
    connectWs()
    remindTimer = setInterval(tickReadyRemind, REMIND_TICK_MS)
  }
})

onUnmounted(() => {
  unbindVoice?.()
  stopPoll()
  for (const id of overdueTimers.keys()) clearOverdueTimer(id)
  if (remindTimer) clearInterval(remindTimer)
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

.voice-unlock {
  margin: 0 0 16px;
  padding: 14px 20px;
  border-radius: 12px;
  background: rgba(82, 196, 26, 0.12);
  border: 1px solid rgba(82, 196, 26, 0.45);
  color: #b7eb8f;
  font-size: clamp(16px, 2vw, 22px);
  cursor: pointer;
  text-align: center;
}
.voice-unlock:hover { background: rgba(82, 196, 26, 0.2); }

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
.panel.ready.has-overdue {
  animation: panel-overdue-glow 1.4s ease-in-out infinite;
  border-color: rgba(255, 77, 79, 0.55);
}
@keyframes panel-overdue-glow {
  0%, 100% { box-shadow: 0 0 24px rgba(255, 77, 79, 0.25); }
  50% { box-shadow: 0 0 48px rgba(255, 77, 79, 0.55); }
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
.overdue-banner {
  margin: 0 0 12px;
  padding: 10px 14px;
  border-radius: 10px;
  background: rgba(255, 77, 79, 0.18);
  border: 1px solid rgba(255, 77, 79, 0.45);
  color: #ffccc7;
  font-size: clamp(16px, 2vw, 22px);
  font-weight: 600;
  text-align: center;
  animation: overdue-flash 1.2s ease-in-out infinite;
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
.ready-ticket.overdue {
  border-radius: 12px;
  margin: 6px -8px;
  padding: 16px 20px;
  border: 2px solid rgba(255, 77, 79, 0.75);
  animation: overdue-card-flash 1.2s ease-in-out infinite;
}
.ready-ticket.overdue .label {
  color: #fff1b8;
  text-shadow: 0 0 12px rgba(255, 214, 102, 0.45);
}
.ready-ticket.overdue .sub { color: #ffccc7; }
.ready-ticket.overdue .state {
  color: #fff;
  font-size: clamp(18px, 2.5vw, 24px);
  font-weight: 800;
}
.pulse {
  animation: pulse 1.6s ease-in-out infinite;
}
.overdue-flash {
  animation: overdue-text-flash 0.9s ease-in-out infinite;
}
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.55; }
}
@keyframes overdue-card-flash {
  0%, 100% {
    background: rgba(255, 77, 79, 0.28);
    box-shadow: 0 0 16px rgba(255, 77, 79, 0.35);
  }
  50% {
    background: rgba(250, 173, 20, 0.38);
    box-shadow: 0 0 32px rgba(255, 77, 79, 0.65);
  }
}
@keyframes overdue-text-flash {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.75; transform: scale(1.06); }
}
@keyframes overdue-flash {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.72; }
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
