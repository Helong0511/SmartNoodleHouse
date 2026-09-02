<template>
  <div class="kitchen" :class="{ fullscreen, standalone }">
    <header class="toolbar">
      <div class="left">
        <div class="head-brand">
          <img src="/logo.png" alt="肠旺面" class="head-logo" />
          <div class="head-text">
            <div class="store-name" :title="storeHint">{{ storeLabel }}</div>
            <div class="head-sub">
              <span class="kds-label">厨房 KDS</span>
              <span class="clock">{{ clockText }}</span>
            </div>
          </div>
        </div>
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
      </div>
      <div class="right">
        <el-radio-group v-model="filter" size="small" class="filters">
          <el-radio-button label="ALL">全部 {{ orders.length }}</el-radio-button>
          <el-radio-button label="NEW">待制作 {{ counts.NEW }}</el-radio-button>
          <el-radio-button label="COOKING">制作中 {{ counts.COOKING }}</el-radio-button>
          <el-radio-button label="READY">待出餐 {{ counts.READY }}</el-radio-button>
          <el-radio-button label="DELIVERY">外卖 {{ counts.DELIVERY }}</el-radio-button>
          <el-radio-button label="DINE">堂食 {{ counts.DINE }}</el-radio-button>
          <el-radio-button label="PACK">打包 {{ counts.PACK }}</el-radio-button>
        </el-radio-group>
        <VoiceSwitch v-model="soundOn" />
        <el-button size="small" type="warning" plain @click="testVoice">试听语音</el-button>
        <el-button size="small" @click="load(true)">刷新</el-button>
        <el-button size="small" type="primary" @click="toggleFullscreen">
          {{ fullscreen ? '退出全屏' : '全屏' }}
        </el-button>
      </div>
    </header>

    <div v-if="soundOn && !voiceUnlocked" class="voice-unlock" @click.stop="enableVoice">
      点击此处或点击屏幕任意位置，启用厨房语音提示
      <span v-if="voiceSessionHint" class="voice-hint-sub">（本页需点击一次，浏览器才允许自动播报）</span>
    </div>

    <div v-if="!filtered.length" class="empty">当前无活跃订单</div>

    <div class="orders-grid">
      <el-card
        v-for="o in filtered"
        :key="o.id"
        class="order-card"
        :class="[
          o.status,
          ageClass(o),
          {
            delivery: o.is_delivery,
            'dine-in': isDineInOrder(o),
            'pack-order': isPackOrder(o) && !o.is_delivery,
          },
        ]"
      >
        <div v-if="o.is_delivery" class="mode-banner delivery">{{ deliveryBanner(o) }}</div>
        <div v-else-if="isPackOrder(o)" class="mode-banner pack">打包出餐</div>
        <div v-else-if="isDineInOrder(o)" class="mode-banner dine">堂食</div>

        <div class="order-header">
          <div class="title-block">
            <div class="table">{{ orderTitle(o) }}</div>
            <div class="badges">
              <el-tag v-if="showStoreTag(o)" size="small" type="success" effect="plain">{{ o.store_name || `店#${o.store_id}` }}</el-tag>
              <el-tag v-if="o.is_member && o.member_level_name" size="small" type="success" effect="plain">{{ o.member_level_name }}</el-tag>
              <el-tag v-if="o.is_delivery" size="small" type="danger" effect="dark" class="mode-tag delivery">{{ o.channel_label || '外卖' }}</el-tag>
              <el-tag v-else-if="isPackOrder(o)" size="small" type="warning" effect="dark" class="mode-tag pack">打包</el-tag>
              <el-tag v-else size="small" type="primary" effect="dark" class="mode-tag dine">堂食</el-tag>
              <el-tag v-if="o.all_no_cooking" size="small" type="warning" effect="plain">现成品·免烹饪</el-tag>
              <el-tag size="small" effect="dark" :type="statusTag(o.status)">{{ statusLabel(o.status) }}</el-tag>
              <el-tag
                v-if="claimLabel(o)"
                size="small"
                :type="o.kitchen_claimed_by_me ? 'success' : 'info'"
                effect="plain"
                class="claim-tag"
              >{{ claimLabel(o) }}</el-tag>
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
          <template v-if="isClaimedByOther(o)">
            <div class="claim-hint">已被「{{ o.kitchen_claimed_by_name || '同事' }}」认领，仅可查看或接管</div>
            <el-button
              type="danger"
              plain
              :loading="busyId === o.id"
              @click="takeoverOrder(o)"
            >接管</el-button>
          </template>
          <template v-else>
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
          </template>
        </div>
      </el-card>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api'
import VoiceSwitch from '@/components/VoiceSwitch.vue'
import { openStaffWebSocket } from '@/utils/ws'
import { useAuthStore } from '@/stores/auth'
import { KDS_ACTIONS, ORDER_STATUS, orderStatusTagType } from '@/utils/labels'
import {
  announceKitchenNew,
  announceKitchenUrge,
  bindAutoVoiceUnlock,
  hasVoiceSessionHint,
  isVoiceUnlocked,
  readVoiceSoundOn,
  stopAllVoice,
  unlockVoice,
  VOICE_SOUND_KEYS,
  writeVoiceSoundOn,
} from '@/utils/voiceAlert'

type Conn = 'connecting' | 'online' | 'offline' | 'polling'

const route = useRoute()
const standalone = computed(() => route.path === '/kds' || !!route.meta?.standalone)
const auth = useAuthStore()
const isSuperAdmin = computed(() => auth.isSuperAdmin)
const orders = ref<any[]>([])
const filter = ref('ALL')
const soundOn = ref(readVoiceSoundOn(VOICE_SOUND_KEYS.kds, true))

watch(soundOn, (on) => {
  writeVoiceSoundOn(VOICE_SOUND_KEYS.kds, on)
  if (!on) stopAllVoice()
})
const voiceUnlocked = ref(isVoiceUnlocked())
const voiceSessionHint = ref(hasVoiceSessionHint())
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
/** 首次列表快照完成后才做 diff，避免与 WS 触发的并发 load 互相覆盖导致漏报 */
let loadSnapshotReady = false
let loadChain: Promise<void> = Promise.resolve()
const recentlyAlertedIds = new Set<number>()

const KITCHEN_NEW_EVENTS = new Set(['ORDER_PAID', 'ORDER_NEW'])

async function enableVoice() {
  voiceUnlocked.value = await unlockVoice('厨房语音已开启')
  if (voiceUnlocked.value) {
    voiceSessionHint.value = false
    ElMessage.success('语音提示已开启')
  } else {
    ElMessage.warning('无法播放，请检查浏览器与系统音量')
  }
}

async function testVoice() {
  if (!soundOn.value) {
    ElMessage.info('请先打开右上角「语音」开关')
    return
  }
  voiceUnlocked.value = await unlockVoice('厨房语音已开启')
  if (!voiceUnlocked.value) {
    ElMessage.warning('无法播放，请检查浏览器与系统音量')
    return
  }
  setTimeout(() => {
    announceKitchenNew({ table_name: '8号桌', table_id: 8 }, true)
  }, 700)
}

async function ensureVoiceFromGesture() {
  if (!soundOn.value || voiceUnlocked.value) return
  voiceUnlocked.value = await unlockVoice()
}

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
    DINE: list.filter((o) => isDineInOrder(o)).length,
    PACK: list.filter((o) => isPackOrder(o) && !o.is_delivery).length,
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
      return list.filter((o) => isDineInOrder(o))
    case 'PACK':
      return list.filter((o) => isPackOrder(o) && !o.is_delivery)
    default:
      return list
  }
})

function statusLabel(s: string) {
  return ORDER_STATUS[s] || s
}

function isPackOrder(o: any) {
  return !!(o?.need_pack || String(o?.dining_type || '').toUpperCase() === 'TAKEAWAY')
}

function isDineInOrder(o: any) {
  return !o?.is_delivery && !isPackOrder(o)
}

function orderTitle(o: any) {
  if (o?.is_delivery) {
    const label = o.channel_label || deliveryPlatformLabel(o.channel)
    return `${label} #${o.delivery?.day_seq || '-'}`
  }
  const table = o.table_name || o.table_no || (o.table_id ? `${o.table_id}号桌` : '')
  if (isPackOrder(o)) {
    return table ? `打包 · ${table}` : '打包取餐'
  }
  return `桌 ${table || '-'}`
}

function deliveryPlatformLabel(channel?: string) {
  const c = String(channel || '').toUpperCase()
  if (c === 'MEITUAN') return '美团外卖'
  if (c === 'ELEME') return '饿了么'
  return '外卖'
}

function deliveryBanner(o: any) {
  const label = o.channel_label || deliveryPlatformLabel(o.channel)
  const seq = o.delivery?.day_seq
  return seq ? `${label} · 序号 ${seq}` : `${label} · 请制作`
}

function statusTag(s: string) {
  return orderStatusTagType(s)
}
const KDS_MAIN_BTN_LABELS = new Set(['开始制作', '进入烹饪', '制作完成', '出餐'])

function isMainKdsBtn(label: string) {
  return KDS_MAIN_BTN_LABELS.has(label)
}

function isClaimedByOther(order: any) {
  return !!(order?.kitchen_claimed_by && !order.kitchen_claimed_by_me)
}

/** 制作中/烹饪中显示认领人；待出餐也显示便于交接 */
function claimLabel(order: any) {
  const name = (order?.kitchen_claimed_by_name || '').trim()
  if (!name) return ''
  if (order.status === 'PREPARING' || order.status === 'COOKING') {
    return order.kitchen_claimed_by_me ? `制作中 · 我（${name}）` : `制作中 · ${name}`
  }
  if (order.status === 'READY') {
    return order.kitchen_claimed_by_me ? `已认领 · 我（${name}）` : `已认领 · ${name}`
  }
  return ''
}

function actionsForOrder(order: any) {
  if (isClaimedByOther(order)) return []
  const status = order?.status
  if (order?.all_no_cooking && (status === 'PAID' || status === 'CONFIRMED')) {
    return [{ status: 'READY', label: '制作完成', type: 'success' }]
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

function alertNewOrder(order: any) {
  if (!soundOn.value) return false
  return announceKitchenNew(order, true)
}

/** 仅在实际解锁后去重，避免未解锁时 WS 抢先占位导致 load 也不再播报 */
function alertNewOrderOnce(order: any) {
  if (!soundOn.value) return
  const id = Number(order?.order_id ?? order?.id)
  if (id && recentlyAlertedIds.has(id)) return
  const spoke = alertNewOrder(order)
  if (!spoke && !isVoiceUnlocked()) return
  if (id) {
    recentlyAlertedIds.add(id)
    setTimeout(() => recentlyAlertedIds.delete(id), 60_000)
  }
}

function alertUrge() {
  if (!soundOn.value) return false
  return announceKitchenUrge(true)
}

function alertUrgeOnce() {
  if (!soundOn.value) return
  if (!isVoiceUnlocked()) {
    announceKitchenUrge(true)
    return
  }
  alertUrge()
}

function tryAlertNewOrders(prevIds: Set<number> | null, data: any[]) {
  if (!prevIds || !soundOn.value) return
  for (const o of data) {
    const isNew = !prevIds.has(o.id)
    const needAlert = isNew && ['PAID', 'CONFIRMED', 'PREPARING'].includes(o.status)
    if (needAlert) {
      alertNewOrderOnce(o)
      return
    }
    if ((o.delivery?.urge_count || 0) > 0) {
      const prev = orders.value.find((x) => x.id === o.id)
      if (!prev || (prev.delivery?.urge_count || 0) < o.delivery.urge_count) {
        alertUrgeOnce()
      }
    }
  }
}

function tryAlertFromWsEvent(event: string, data: Record<string, any>, beforeIds: Set<number>) {
  if (!soundOn.value) return
  if (event === 'ORDER_URGE') {
    alertUrgeOnce()
    return
  }
  if (!KITCHEN_NEW_EVENTS.has(event)) return
  const oid = Number(data.order_id)
  if (!oid || beforeIds.has(oid)) return
  const full = orders.value.find((o) => o.id === oid)
  alertNewOrderOnce(full || data)
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
  loadChain = loadChain.then(() => loadOnce(manual)).catch(() => loadOnce(manual))
  await loadChain
}

async function loadOnce(manual = false) {
  if (manual && soundOn.value && !voiceUnlocked.value) {
    voiceUnlocked.value = await unlockVoice('厨房语音已开启')
  }
  try {
    const data = (await api.get('/orders/kitchen/active', { params: kitchenParams() })).data || []
    const nextIds = new Set<number>(data.map((o: any) => o.id))
    const prevIds = loadSnapshotReady ? new Set(knownIds) : null
    tryAlertNewOrders(prevIds, data)
    knownIds = nextIds
    loadSnapshotReady = true
    orders.value = data
    if (manual) ElMessage.success('已刷新')
  } catch (e: any) {
    if (manual) ElMessage.error(e.message || '加载失败')
  }
}

function onStoreChange() {
  knownIds = new Set()
  loadSnapshotReady = false
  recentlyAlertedIds.clear()
  load(true)
  connectWs()
}

async function updateStatus(id: number, status: string, forceTakeover = false) {
  await ensureVoiceFromGesture()
  busyId.value = id
  try {
    await api.patch(`/orders/${id}/status`, { status, force_takeover: forceTakeover })
    await load()
  } catch (e: any) {
    const msg = e.message || '操作失败'
    // 40028：已被他人认领 → 引导接管
    if (String(msg).includes('认领') || String(msg).includes('接管')) {
      try {
        await ElMessageBox.confirm(`${msg}\n是否立即接管并继续？`, '订单已被认领', {
          confirmButtonText: '接管并继续',
          cancelButtonText: '取消',
          type: 'warning',
        })
        await api.patch(`/orders/${id}/status`, { status, force_takeover: true })
        await load()
        return
      } catch {
        /* 用户取消 */
      }
    }
    ElMessage.error(msg)
  } finally {
    busyId.value = null
  }
}

async function takeoverOrder(o: any) {
  await ensureVoiceFromGesture()
  const name = o.kitchen_claimed_by_name || '同事'
  try {
    await ElMessageBox.confirm(`订单当前由「${name}」认领，确定接管？`, '接管订单', {
      confirmButtonText: '确认接管',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return
  }
  busyId.value = o.id
  try {
    await api.post(`/orders/${o.id}/kitchen-takeover`)
    ElMessage.success('已接管')
    await load()
  } catch (e: any) {
    ElMessage.error(e.message || '接管失败')
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
  ws.onmessage = async (ev) => {
    let event = ''
    let data: Record<string, any> = {}
    try {
      const msg = JSON.parse(ev.data || '{}')
      event = String(msg.event || '')
      data = msg.data || {}
    } catch {
      /* ignore malformed ws payload */
    }
    const beforeIds = new Set(knownIds)
    await load()
    tryAlertFromWsEvent(event, data, beforeIds)
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
  if (soundOn.value && !voiceUnlocked.value) {
    unlockVoice('厨房语音已开启').then((ok) => {
      voiceUnlocked.value = ok
    })
  }
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

let unbindVoice: (() => void) | null = null

onMounted(async () => {
  if (standalone.value) document.title = '厨房 KDS'
  voiceUnlocked.value = isVoiceUnlocked()
  unbindVoice = bindAutoVoiceUnlock((ok) => {
    if (ok) {
      voiceUnlocked.value = true
      voiceSessionHint.value = false
    }
  })

  await loadStores()
  await load()
  connectWs()
  tickClock()
  clockTimer = setInterval(tickClock, 1000)
  ageTimer = setInterval(() => {
    nowTick.value = Date.now()
  }, 5000)
  document.addEventListener('fullscreenchange', onFsChange)
})

onUnmounted(() => {
  unbindVoice?.()
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
.kitchen.standalone {
  min-height: 100vh;
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
.head-brand {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}
.head-logo {
  flex-shrink: 0;
  width: 48px;
  height: auto;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.92);
}
.head-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.store-name {
  font-size: clamp(22px, 3.2vw, 32px);
  font-weight: 800;
  line-height: 1.15;
  letter-spacing: 0.5px;
  color: #f8fafc;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: min(420px, 42vw);
}
.head-sub {
  display: flex;
  align-items: baseline;
  gap: 12px;
  flex-wrap: wrap;
}
.kds-label {
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0.5px;
  color: #64748b;
  text-transform: uppercase;
}
.clock {
  color: #e2e8f0;
  font-variant-numeric: tabular-nums;
  font-size: clamp(20px, 2.6vw, 28px);
  font-weight: 700;
  letter-spacing: 0.5px;
}

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

.voice-unlock {
  margin: 8px 0 0;
  padding: 12px 16px;
  border-radius: 8px;
  background: rgba(245, 158, 11, 0.22);
  border: 1px solid rgba(245, 158, 11, 0.55);
  color: #fde68a;
  font-size: 15px;
  font-weight: 600;
  cursor: pointer;
  text-align: center;
  position: sticky;
  top: 72px;
  z-index: 6;
  animation: voice-pulse 2s ease-in-out infinite;
}
.voice-unlock:hover { background: rgba(245, 158, 11, 0.32); }
.voice-hint-sub {
  display: block;
  margin-top: 4px;
  font-size: 12px;
  font-weight: 500;
  opacity: 0.85;
}
@keyframes voice-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(245, 158, 11, 0.35); }
  50% { box-shadow: 0 0 0 8px rgba(245, 158, 11, 0); }
}

.orders-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 14px;
  margin-top: 8px;
}

.order-card {
  position: relative;
  background: #16213e;
  border: 1px solid #243056;
  color: #eef2ff;
  border-radius: 12px;
  overflow: hidden;
  --el-card-bg-color: #16213e;
  --el-card-border-color: #243056;
}
.order-card :deep(.el-card__body) { padding: 14px; }
.order-card.PAID, .order-card.CONFIRMED { border-left: 4px solid #e6a23c; }
.order-card.PREPARING { border-left: 4px solid #409eff; }
.order-card.COOKING { border-left: 4px solid #a78bfa; }
.order-card.READY { border-left: 4px solid #67c23a; }
.order-card.delivery {
  background: linear-gradient(160deg, #2a1020 0%, #1a1528 55%, #141828 100%);
  border: 2px solid #e11d48;
  box-shadow:
    0 0 0 1px rgba(244, 63, 94, 0.4),
    inset 0 0 28px rgba(225, 29, 72, 0.1);
  animation: delivery-glow 2.6s ease-in-out infinite;
}
.order-card.delivery.PAID,
.order-card.delivery.CONFIRMED { border-left-width: 6px; border-left-color: #fb7185; }
.order-card.delivery .table {
  color: #fda4af;
  text-shadow: 0 0 14px rgba(251, 113, 133, 0.35);
}
.order-card.delivery.age-critical {
  animation: delivery-glow 2.6s ease-in-out infinite, pulse 1.6s ease-in-out infinite;
}
.order-card.dine-in {
  background: linear-gradient(160deg, #121c33 0%, #16213e 100%);
  border-color: #2d4a6f;
  box-shadow: inset 0 0 0 1px rgba(64, 158, 255, 0.18);
}
.order-card.dine-in .table { color: #bfdbfe; }
.order-card.pack-order {
  background: linear-gradient(160deg, #2a1808 0%, #221a12 55%, #1a1520 100%);
  border: 2px solid #f59e0b;
  box-shadow:
    0 0 0 1px rgba(245, 158, 11, 0.45),
    inset 0 0 28px rgba(245, 158, 11, 0.1);
  animation: pack-glow 2.4s ease-in-out infinite;
}
.order-card.pack-order.PAID,
.order-card.pack-order.CONFIRMED { border-left-width: 6px; border-left-color: #fb923c; }
.order-card.pack-order .table {
  color: #fde68a;
  text-shadow: 0 0 14px rgba(251, 191, 36, 0.35);
}
.order-card.age-warn { outline: 1px solid rgba(245, 158, 11, 0.55); }
.order-card.age-critical {
  outline: 2px solid rgba(239, 68, 68, 0.7);
  animation: pulse 1.6s ease-in-out infinite;
}
.order-card.pack-order.age-critical {
  animation: pack-glow 2.4s ease-in-out infinite, pulse 1.6s ease-in-out infinite;
}

.mode-banner {
  margin: -14px -14px 10px;
  padding: 6px 12px;
  font-size: 13px;
  font-weight: 800;
  letter-spacing: 1px;
  text-align: center;
}
.mode-banner.dine {
  background: rgba(59, 130, 246, 0.22);
  color: #93c5fd;
  border-bottom: 1px solid rgba(96, 165, 250, 0.35);
}
.mode-banner.delivery {
  background: linear-gradient(90deg, #be123c, #e11d48);
  color: #fff1f2;
  border-bottom: 1px solid rgba(251, 113, 133, 0.55);
}
.mode-banner.pack {
  background: linear-gradient(90deg, #ea580c, #f59e0b);
  color: #1a1208;
  border-bottom: 1px solid rgba(251, 191, 36, 0.6);
}
.mode-tag.dine { font-weight: 700; }
.mode-tag.delivery { font-weight: 800; }
.mode-tag.pack { font-weight: 800; }

@keyframes delivery-glow {
  0%, 100% { box-shadow: 0 0 0 1px rgba(244, 63, 94, 0.4), inset 0 0 28px rgba(225, 29, 72, 0.08); }
  50% { box-shadow: 0 0 0 3px rgba(251, 113, 133, 0.5), inset 0 0 36px rgba(225, 29, 72, 0.14); }
}

@keyframes pack-glow {
  0%, 100% { box-shadow: 0 0 0 1px rgba(245, 158, 11, 0.45), inset 0 0 28px rgba(245, 158, 11, 0.08); }
  50% { box-shadow: 0 0 0 3px rgba(251, 191, 36, 0.55), inset 0 0 36px rgba(245, 158, 11, 0.16); }
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

.delivery-info {
  font-size: 13px;
  color: #fecdd3;
  background: rgba(190, 18, 60, 0.18);
  border: 1px solid rgba(251, 113, 133, 0.35);
  border-radius: 6px;
  padding: 8px 10px;
  margin-bottom: 8px;
  line-height: 1.45;
}
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
.claim-hint {
  width: 100%;
  font-size: 13px;
  color: #909399;
  line-height: 1.4;
}
.claim-tag { max-width: 100%; }
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
