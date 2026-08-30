<template>
  <div class="kitchen" :class="{ fullscreen }">
    <header class="toolbar">
      <div class="left">
        <h2>厨房 KDS</h2>
        <span class="store-chip" :title="storeHint">{{ storeLabel }}</span>
        <el-select
          v-if="isSuperAdmin"
          v-model="selectedStoreId"
          clearable
          placeholder="全部门店"
          size="small"
          style="width: 160px"
          @change="onStoreChange"
        >
          <el-option label="全部门店" :value="null" />
          <el-option v-for="s in storeOptions" :key="s.id" :label="s.name" :value="s.id" />
        </el-select>
        <span class="conn" :class="connStatus">
          <i class="dot" />
          {{ connLabel }}
        </span>
        <span class="clock">{{ clockText }}</span>
      </div>
      <div class="right">
        <el-radio-group v-model="filter" size="small" class="filters">
          <el-radio-button label="ALL">全部 {{ orders.length }}</el-radio-button>
          <el-radio-button label="NEW">待制作 {{ counts.NEW }}</el-radio-button>
          <el-radio-button label="COOKING">制作中 {{ counts.COOKING }}</el-radio-button>
          <el-radio-button label="READY">待出餐 {{ counts.READY }}</el-radio-button>
          <el-radio-button label="DELIVERY">外卖 {{ counts.DELIVERY }}</el-radio-button>
          <el-radio-button label="DINE">堂食 {{ counts.DINE }}</el-radio-button>
        </el-radio-group>
        <el-switch v-model="soundOn" inline-prompt active-text="音" inactive-text="静" />
        <el-button size="small" @click="load(true)">刷新</el-button>
        <el-button size="small" type="primary" @click="toggleFullscreen">
          {{ fullscreen ? '退出全屏' : '全屏' }}
        </el-button>
      </div>
    </header>

    <div v-if="!filtered.length" class="empty">当前无活跃订单</div>

    <div class="orders-grid">
      <el-card
        v-for="o in filtered"
        :key="o.id"
        class="order-card"
        :class="[o.status, ageClass(o), { delivery: o.is_delivery, pack: o.need_pack || o.dining_type === 'TAKEAWAY' }]"
      >
        <div class="order-header">
          <div class="title-block">
            <div class="table">
              <template v-if="o.is_delivery">
                {{ o.channel_label }} #{{ o.delivery?.day_seq || '-' }}
              </template>
              <template v-else>
                桌 {{ o.table_name || o.table_no || o.table_id || '-' }}
              </template>
            </div>
            <div class="badges">
              <el-tag v-if="showStoreTag(o)" size="small" type="success" effect="plain">{{ o.store_name || `店#${o.store_id}` }}</el-tag>
              <el-tag v-if="o.is_member && o.member_level_name" size="small" type="success" effect="plain">{{ o.member_level_name }}</el-tag>
              <el-tag size="small" :type="o.is_delivery ? 'danger' : 'info'">{{ o.channel_label || '堂食' }}</el-tag>
              <el-tag v-if="o.need_pack || o.dining_type === 'TAKEAWAY'" size="small" type="warning">打包</el-tag>
              <el-tag v-if="o.all_no_cooking" size="small" type="warning" effect="plain">现成品·直出</el-tag>
              <el-tag size="small" effect="dark" :type="statusTag(o.status)">{{ statusLabel(o.status) }}</el-tag>
            </div>
          </div>
          <div class="meta">
            <div class="wait" :class="ageClass(o)">⏱ {{ formatWait(o) }}</div>
            <div class="ono">{{ o.order_no }}</div>
          </div>
        </div>

        <div v-if="o.is_delivery && o.delivery" class="delivery-info">
          <div>{{ o.delivery.address }}</div>
          <div>{{ o.delivery.phone }}</div>
          <div v-if="o.delivery.rider_name">骑手：{{ o.delivery.rider_name }} {{ o.delivery.rider_phone }}</div>
          <div v-if="o.delivery.logistics_status">配送：{{ o.delivery.logistics_status }}</div>
        </div>

        <div v-if="o.delivery?.urge_count > 0" class="alert urge">催单 ×{{ o.delivery.urge_count }}</div>
        <div v-if="o.delivery?.refund_status === 'PENDING'" class="alert refund">退款申请中</div>
        <div v-if="o.remark" class="remark">备注：{{ o.remark }}</div>

        <div class="items">
          <div
            v-for="item in displayItems(o.items)"
            :key="itemKey(item)"
            class="item"
            :class="qtyClass(item.quantity)"
          >
            <strong class="item-line">
              <span class="item-name">
                {{ item.product_name }}
                <el-tag v-if="item.no_cooking" size="small" type="warning" effect="plain" class="item-tag">免烹饪</el-tag>
              </span>
              <span class="item-qty">×{{ item.quantity }}</span>
            </strong>
            <div v-if="item.options?.length" class="opts">{{ item.options.join(' / ') }}</div>
            <div v-if="item.remark" class="item-remark">{{ item.remark }}</div>
          </div>
        </div>

        <div v-if="Number(o.packing_fee) > 0" class="pack-fee">含打包费 ¥{{ o.packing_fee }}</div>

        <div class="actions">
          <el-button
            v-for="act in actionsForOrder(o)"
            :key="act.status"
            :type="(act.type || 'default') as any"
            :plain="!!act.plain"
            :class="{ 'kds-btn-main': isMainKdsBtn(act.label) }"
            :loading="busyId === o.id"
            @click="updateStatus(o.id, act.status)"
          >
            {{ act.label }}
          </el-button>
        </div>
      </el-card>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api'
import { openStaffWebSocket } from '@/utils/ws'
import { useAuthStore } from '@/stores/auth'
import { KDS_ACTIONS, ORDER_STATUS, orderStatusTagType } from '@/utils/labels'

type Conn = 'connecting' | 'online' | 'offline' | 'polling'

const auth = useAuthStore()
const isSuperAdmin = computed(() => auth.isSuperAdmin)
const orders = ref<any[]>([])
const filter = ref('ALL')
const soundOn = ref(true)
const fullscreen = ref(false)
const connStatus = ref<Conn>('connecting')
const clockText = ref('')
const busyId = ref<number | null>(null)
const nowTick = ref(Date.now())
const storeOptions = ref<{ id: number; name: string; store_no?: string }[]>([])
const selectedStoreId = ref<number | null>(null)
const boundStoreName = ref('')

const storeLabel = computed(() => {
  if (isSuperAdmin.value) {
    if (!selectedStoreId.value) return '全部门店'
    const s = storeOptions.value.find((x) => x.id === selectedStoreId.value)
    return s?.name || `门店 #${selectedStoreId.value}`
  }
  return boundStoreName.value || (auth.user?.store_id ? `本店 #${auth.user.store_id}` : '本店')
})
const storeHint = computed(() =>
  isSuperAdmin.value
    ? '超管可切换门店；选「全部门店」时卡片会显示店名以免桌号冲突'
    : '当前账号仅能查看绑定门店订单',
)

function showStoreTag(o: any) {
  // 超管看全店，或本批订单含多店时显示
  if (isSuperAdmin.value && !selectedStoreId.value) return true
  const ids = new Set(orders.value.map((x) => x.store_id).filter(Boolean))
  return ids.size > 1
}

let ws: WebSocket | null = null
let pollTimer: ReturnType<typeof setInterval> | null = null
let clockTimer: ReturnType<typeof setInterval> | null = null
let ageTimer: ReturnType<typeof setInterval> | null = null
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
let reconnectAttempt = 0
let knownIds = new Set<number>()
let audioCtx: AudioContext | null = null

const connLabel = computed(() => {
  const m: Record<Conn, string> = {
    connecting: '连接中',
    online: '实时推送',
    offline: '已断开',
    polling: '轮询兜底',
  }
  return m[connStatus.value]
})

const counts = computed(() => {
  const list = orders.value
  return {
    NEW: list.filter((o) => o.status === 'PAID' || o.status === 'CONFIRMED').length,
    COOKING: list.filter((o) => o.status === 'PREPARING' || o.status === 'COOKING').length,
    READY: list.filter((o) => o.status === 'READY').length,
    DELIVERY: list.filter((o) => o.is_delivery).length,
    DINE: list.filter((o) => !o.is_delivery).length,
  }
})

const filtered = computed(() => {
  const list = [...orders.value].sort((a, b) => {
    // 催单优先，再按等待时间
    const ua = a.delivery?.urge_count || 0
    const ub = b.delivery?.urge_count || 0
    if (ub !== ua) return ub - ua
    return waitSeconds(b) - waitSeconds(a)
  })
  switch (filter.value) {
    case 'NEW':
      return list.filter((o) => o.status === 'PAID' || o.status === 'CONFIRMED')
    case 'COOKING':
      return list.filter((o) => o.status === 'PREPARING' || o.status === 'COOKING')
    case 'READY':
      return list.filter((o) => o.status === 'READY')
    case 'DELIVERY':
      return list.filter((o) => o.is_delivery)
    case 'DINE':
      return list.filter((o) => !o.is_delivery)
    default:
      return list
  }
})

function statusLabel(s: string) {
  return ORDER_STATUS[s] || s
}
function statusTag(s: string) {
  return orderStatusTagType(s)
}
const KDS_MAIN_BTN_LABELS = new Set(['开始制作', '进入烹饪', '制作完成', '出餐', '直接出餐'])

function isMainKdsBtn(label: string) {
  return KDS_MAIN_BTN_LABELS.has(label)
}

function actionsForOrder(order: any) {
  const status = order?.status
  if (order?.all_no_cooking && (status === 'PAID' || status === 'CONFIRMED')) {
    return [{ status: 'COMPLETED', label: '直接出餐', type: 'success' }]
  }
  return KDS_ACTIONS[status] || []
}

/** 相同品名+规格+备注合并为一行，份数相加（如两条 ×2 显示 ×4） */
function displayItems(items: any[] | undefined) {
  const map = new Map<string, any>()
  for (const raw of items || []) {
    const opts = [...(raw.options || [])].sort().join('|')
    const key = `${raw.product_name || ''}\0${opts}\0${raw.remark || ''}`
    const qty = Number(raw.quantity) || 1
    const cur = map.get(key)
    if (cur) {
      cur.quantity += qty
      cur.no_cooking = cur.no_cooking || !!raw.no_cooking
      if (raw.id != null) cur.ids.push(raw.id)
    } else {
      map.set(key, {
        product_name: raw.product_name,
        quantity: qty,
        options: raw.options || [],
        remark: raw.remark,
        no_cooking: !!raw.no_cooking,
        ids: raw.id != null ? [raw.id] : [],
      })
    }
  }
  return Array.from(map.values())
}

function itemKey(item: any) {
  const opts = [...(item.options || [])].sort().join('|')
  return `${item.product_name}|${opts}|${item.remark || ''}|${item.quantity}`
}

/** 1 份正常色；2 份醒目；3 份及以上更醒目 */
function qtyClass(quantity: number) {
  const q = Number(quantity) || 1
  if (q >= 3) return 'qty-high'
  if (q >= 2) return 'qty-multi'
  return 'qty-one'
}

function waitSeconds(o: any) {
  if (o.paid_at) {
    const t = Date.parse(o.paid_at)
    if (!Number.isNaN(t)) return Math.max(0, Math.floor((nowTick.value - t) / 1000))
  }
  return Number(o.wait_seconds || 0)
}

function formatWait(o: any) {
  const s = waitSeconds(o)
  const m = Math.floor(s / 60)
  const r = s % 60
  if (m >= 60) return `${Math.floor(m / 60)}h${m % 60}m`
  return `${m}:${String(r).padStart(2, '0')}`
}

function ageClass(o: any) {
  const m = waitSeconds(o) / 60
  if (m >= 20 || (o.delivery?.urge_count || 0) > 0) return 'age-critical'
  if (m >= 10) return 'age-warn'
  return 'age-ok'
}

function beep(kind: 'new' | 'urge' = 'new') {
  if (!soundOn.value) return
  try {
    if (!audioCtx) audioCtx = new AudioContext()
    const ctx = audioCtx
    const osc = ctx.createOscillator()
    const gain = ctx.createGain()
    osc.connect(gain)
    gain.connect(ctx.destination)
    osc.frequency.value = kind === 'urge' ? 880 : 660
    gain.gain.value = 0.08
    osc.start()
    setTimeout(() => {
      osc.stop()
    }, kind === 'urge' ? 280 : 160)
    if (kind === 'urge') {
      setTimeout(() => beep('new'), 320)
    }
  } catch {
    /* ignore */
  }
}

async function loadStores() {
  try {
    const res = await api.get('/orders/kitchen/stores')
    const data = res.data || {}
    storeOptions.value = data.items || []
    if (!isSuperAdmin.value && storeOptions.value.length) {
      boundStoreName.value = storeOptions.value[0].name
      selectedStoreId.value = storeOptions.value[0].id
    }
  } catch (e: any) {
    ElMessage.error(e.message || '加载门店失败')
  }
}

function kitchenParams() {
  const params: Record<string, any> = {}
  if (isSuperAdmin.value && selectedStoreId.value) {
    params.store_id = selectedStoreId.value
  }
  return params
}

async function load(manual = false) {
  try {
    const data = (await api.get('/orders/kitchen/active', { params: kitchenParams() })).data || []
    const nextIds = new Set<number>(data.map((o: any) => o.id))
    if (knownIds.size) {
      for (const o of data) {
        if (!knownIds.has(o.id) && (o.status === 'PAID' || o.status === 'CONFIRMED')) {
          beep('new')
          break
        }
        if ((o.delivery?.urge_count || 0) > 0) {
          const prev = orders.value.find((x) => x.id === o.id)
          if (!prev || (prev.delivery?.urge_count || 0) < o.delivery.urge_count) {
            beep('urge')
          }
        }
      }
    }
    knownIds = nextIds
    orders.value = data
    if (manual) ElMessage.success('已刷新')
  } catch (e: any) {
    if (manual) ElMessage.error(e.message || '加载失败')
  }
}

function onStoreChange() {
  knownIds = new Set()
  load(true)
  connectWs()
}

async function updateStatus(id: number, status: string) {
  busyId.value = id
  try {
    await api.patch(`/orders/${id}/status`, { status })
    await load()
  } catch (e: any) {
    ElMessage.error(e.message || '操作失败')
  } finally {
    busyId.value = null
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
  const delay = Math.min(15000, 1000 * Math.pow(2, reconnectAttempt++))
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null
    connectWs()
  }, delay)
}

function connectWs() {
  try {
    ws?.close()
  } catch {
    /* */
  }
  connStatus.value = 'connecting'
  const params: Record<string, string> = {}
  if (isSuperAdmin.value && selectedStoreId.value) {
    params.store_id = String(selectedStoreId.value)
  }
  ws = openStaffWebSocket('kitchen', params)

  ws.onopen = () => {
    reconnectAttempt = 0
    connStatus.value = 'online'
    stopPoll()
  }
  ws.onmessage = (ev) => {
    try {
      const msg = JSON.parse(ev.data || '{}')
      const event = msg.event || ''
      if (event === 'ORDER_URGE') beep('urge')
      else if (event === 'ORDER_PAID' || event === 'ORDER_CREATED' || event === 'ORDER_NEW') beep('new')
    } catch {
      /* */
    }
    load()
  }
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
  const d = new Date()
  clockText.value = d.toLocaleTimeString('zh-CN', { hour12: false })
  nowTick.value = d.getTime()
}

onMounted(async () => {
  await loadStores()
  load()
  connectWs()
  tickClock()
  clockTimer = setInterval(tickClock, 1000)
  ageTimer = setInterval(() => {
    nowTick.value = Date.now()
  }, 5000)
  document.addEventListener('fullscreenchange', onFsChange)
})

onUnmounted(() => {
  ws?.close()
  stopPoll()
  if (reconnectTimer) clearTimeout(reconnectTimer)
  if (clockTimer) clearInterval(clockTimer)
  if (ageTimer) clearInterval(ageTimer)
  document.removeEventListener('fullscreenchange', onFsChange)
})
</script>

<style scoped>
.kitchen {
  padding: 12px 16px 24px;
  background: #0f1220;
  min-height: calc(100vh - 56px);
  color: #eef2ff;
}
.kitchen.fullscreen {
  position: fixed;
  inset: 0;
  z-index: 4000;
  min-height: 100vh;
  overflow: auto;
}

.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  position: sticky;
  top: 0;
  z-index: 5;
  background: rgba(15, 18, 32, 0.95);
  padding: 8px 0 12px;
  backdrop-filter: blur(6px);
}
.left, .right { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.store-chip {
  font-size: 13px;
  padding: 2px 10px;
  border-radius: 999px;
  background: rgba(64, 158, 255, 0.18);
  color: #93c5fd;
  border: 1px solid rgba(147, 197, 253, 0.35);
}
h2 { margin: 0; font-size: 22px; letter-spacing: 1px; }
.clock { color: #9aa4c7; font-variant-numeric: tabular-nums; font-size: 14px; }

.conn {
  display: inline-flex; align-items: center; gap: 6px;
  font-size: 12px; padding: 2px 10px; border-radius: 999px;
  background: #1b2140; color: #9aa4c7;
}
.conn .dot { width: 8px; height: 8px; border-radius: 50%; background: #666; }
.conn.online { color: #86efac; }
.conn.online .dot { background: #22c55e; box-shadow: 0 0 8px #22c55e; }
.conn.polling { color: #fde68a; }
.conn.polling .dot { background: #f59e0b; }
.conn.offline, .conn.connecting { color: #fca5a5; }
.conn.offline .dot, .conn.connecting .dot { background: #ef4444; }

.filters :deep(.el-radio-button__inner) {
  background: #1b2140; border-color: #2a335a; color: #c7d2fe;
}
.filters :deep(.el-radio-button__original-radio:checked + .el-radio-button__inner) {
  background: #334155; border-color: #64748b; box-shadow: none;
}

.empty {
  margin-top: 80px; text-align: center; color: #64748b; font-size: 18px;
}

.orders-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 14px;
  margin-top: 8px;
}

.order-card {
  background: #16213e;
  border: 1px solid #243056;
  color: #eef2ff;
  border-radius: 12px;
  --el-card-bg-color: #16213e;
  --el-card-border-color: #243056;
}
.order-card :deep(.el-card__body) { padding: 14px; }
.order-card.PAID, .order-card.CONFIRMED { border-left: 4px solid #e6a23c; }
.order-card.PREPARING { border-left: 4px solid #409eff; }
.order-card.COOKING { border-left: 4px solid #a78bfa; }
.order-card.READY { border-left: 4px solid #67c23a; }
.order-card.delivery { background: #1a2740; }
.order-card.pack { box-shadow: inset 0 0 0 1px rgba(230, 162, 60, 0.35); }
.order-card.age-warn { outline: 1px solid rgba(245, 158, 11, 0.55); }
.order-card.age-critical {
  outline: 2px solid rgba(239, 68, 68, 0.7);
  animation: pulse 1.6s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(239, 68, 68, 0.25); }
  50% { box-shadow: 0 0 0 6px rgba(239, 68, 68, 0); }
}

.order-header { display: flex; justify-content: space-between; gap: 8px; margin-bottom: 10px; }
.title-block { min-width: 0; }
.table { font-size: 22px; font-weight: 800; line-height: 1.2; }
.badges { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 6px; }
.meta { text-align: right; flex-shrink: 0; }
.wait {
  font-size: 20px; font-weight: 700; font-variant-numeric: tabular-nums;
  color: #86efac;
}
.wait.age-warn { color: #fbbf24; }
.wait.age-critical { color: #f87171; }
.ono { font-size: 11px; color: #94a3b8; margin-top: 4px; max-width: 140px; overflow: hidden; text-overflow: ellipsis; }

.delivery-info { font-size: 13px; color: #f5a623; margin-bottom: 8px; line-height: 1.45; }
.remark {
  background: #2a1f14; color: #fdba74; padding: 6px 8px; border-radius: 6px;
  font-size: 13px; margin-bottom: 8px;
}
.alert { padding: 4px 8px; border-radius: 4px; font-size: 12px; margin-bottom: 8px; font-weight: 700; }
.alert.urge { background: #e6a23c; color: #1a1a2e; }
.alert.refund { background: #f56c6c; color: #fff; }

.items { margin: 4px 0 8px; }
.item { margin: 8px 0; padding-bottom: 6px; border-bottom: 1px dashed #2a335a; }
.item:last-child { border-bottom: none; }
.item-line { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
.item-name { flex: 1; min-width: 0; display: flex; align-items: center; flex-wrap: wrap; gap: 6px; }
.item-tag { vertical-align: middle; }
.item-qty {
  flex-shrink: 0;
  font-variant-numeric: tabular-nums;
  font-weight: 800;
}
/* 1 份：与正文同色 */
.item.qty-one .item-qty { color: #eef2ff; font-size: 16px; }
/* 2 份：琥珀色提醒 */
.item.qty-multi .item-qty {
  color: #fbbf24;
  font-size: 22px;
  text-shadow: 0 0 10px rgba(251, 191, 36, 0.45);
}
.item.qty-multi .item-name { color: #fde68a; }
/* 3 份及以上：红色高亮 */
.item.qty-high .item-qty {
  color: #f87171;
  font-size: 26px;
  text-shadow: 0 0 12px rgba(248, 113, 113, 0.55);
}
.item.qty-high .item-name { color: #fecaca; font-weight: 700; }
.item.qty-high {
  background: rgba(239, 68, 68, 0.08);
  border-radius: 6px;
  padding: 6px 8px;
  margin-left: -8px;
  margin-right: -8px;
}
.opts { color: #94a3b8; font-size: 13px; margin-top: 2px; }
.item-remark { color: #fdba74; font-size: 12px; margin-top: 2px; }
.pack-fee { color: #fbbf24; font-size: 12px; margin-bottom: 8px; }
.actions { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 10px; }
.actions :deep(.el-button.kds-btn-main) {
  flex: 1 1 100%;
  width: 100%;
  font-size: 18px;
  font-weight: 700;
  padding: 14px 20px;
  min-height: 50px;
  letter-spacing: 0.5px;
}
</style>
