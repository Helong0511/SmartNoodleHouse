<template>
  <div v-loading="loading" class="welcome-page">
    <div class="kpi-row">
      <div class="kpi" v-for="k in kpiCards" :key="k.label">
        <div class="kpi-val">{{ k.value }}</div>
        <div class="kpi-label">{{ k.label }}</div>
      </div>
      <div class="kpi-actions">
        <el-tag size="small" :type="connStatus === 'online' ? 'success' : 'info'">{{ connLabel }}</el-tag>
        <el-button @click="refreshAll">刷新</el-button>
      </div>
    </div>

    <el-form inline class="toolbar">
      <el-form-item v-if="auth.isSuperAdmin">
        <el-select
          v-model="storeFilter"
          placeholder="全部门店"
          clearable
          style="width:160px"
          @change="onStoreChange"
        >
          <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
        </el-select>
      </el-form-item>
      <el-tag v-else-if="boundStoreName" type="info" style="margin-right:12px">门店：{{ boundStoreName }}</el-tag>
      <el-form-item>
        <el-input v-model="filters.customerId" placeholder="顾客ID" clearable style="width:110px" @keyup.enter="reload" @clear="reload" />
      </el-form-item>
      <el-form-item>
        <el-input v-model="filters.keyword" placeholder="欢迎语关键词" clearable style="width:160px" @keyup.enter="reload" @clear="reload" />
      </el-form-item>
      <el-form-item>
        <el-select
          v-model="filters.cameraId"
          placeholder="摄像头"
          clearable
          filterable
          style="width:180px"
          @change="reload"
        >
          <el-option v-for="c in cameras" :key="c.id" :label="cameraLabel(c)" :value="c.id" />
        </el-select>
      </el-form-item>
      <el-form-item>
        <el-date-picker
          v-model="filters.dateRange"
          type="daterange"
          value-format="YYYY-MM-DD"
          start-placeholder="开始"
          end-placeholder="结束"
          style="width:240px"
          @change="reload"
        />
      </el-form-item>
      <el-button type="primary" @click="reload">查询</el-button>
      <el-button @click="resetFilters">重置</el-button>
    </el-form>

    <div v-if="hasNewHint" class="new-hint" @click="jumpToLatest">
      有新的迎宾记录，点击回到第 1 页查看最新
    </div>

    <el-table :data="logs" border size="small" stripe>
      <el-table-column label="时间" width="170">
        <template #default="{ row }">{{ fmt(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="顾客" min-width="140">
        <template #default="{ row }">
          <div>{{ row.customer_name || '—' }}</div>
          <div class="sub">ID {{ row.customer_id }} · {{ row.customer_no || '-' }}</div>
        </template>
      </el-table-column>
      <el-table-column label="摄像头" width="140">
        <template #default="{ row }">
          {{ row.camera_name || (row.camera_id != null ? `#${row.camera_id}` : '—') }}
        </template>
      </el-table-column>
      <el-table-column prop="text" label="欢迎语" min-width="280" show-overflow-tooltip />
    </el-table>

    <div class="pager">
      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :total="total"
        :page-sizes="[20, 50, 100]"
        layout="total, sizes, prev, pager, next"
        background
        small
        @current-change="load"
        @size-change="reload"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api'
import { openStaffWebSocket } from '@/utils/ws'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const loading = ref(false)
const logs = ref<any[]>([])
const stores = ref<any[]>([])
const cameras = ref<any[]>([])
const storeFilter = ref<number | undefined>()
const page = ref(1)
const pageSize = ref(50)
const total = ref(0)
// 非第 1 页时实时事件不替换列表，仅提示，避免高频日志写入导致翻页跳动
const hasNewHint = ref(false)
const stats = reactive({ total: 0, today: 0, unique_customers: 0 })

function todayStr() {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

const filters = reactive({
  customerId: '',
  keyword: '',
  cameraId: undefined as number | undefined,
  dateRange: [todayStr(), todayStr()] as string[] | null,
})

const boundStoreName = computed(() => {
  const sid = auth.user?.store_id
  if (!sid) return ''
  return stores.value.find((s) => s.id === sid)?.name || `门店#${sid}`
})

const kpiCards = computed(() => [
  { label: '今日迎宾', value: stats.today },
  { label: '累计迎宾', value: stats.total },
  { label: '迎宾会员数', value: stats.unique_customers },
])

const connStatus = ref<'online' | 'polling' | 'offline' | 'connecting'>('connecting')
const connLabel = computed(() => {
  const m = { online: '实时在线', polling: '轮询中', offline: '已离线', connecting: '连接中…' }
  return m[connStatus.value]
})

let ws: WebSocket | null = null
let pollTimer: ReturnType<typeof setInterval> | null = null
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
let reconnectAttempt = 0

function fmt(v?: string) {
  return v ? String(v).replace('T', ' ').slice(0, 19) : '-'
}

function resetFilters() {
  storeFilter.value = undefined
  filters.customerId = ''
  filters.keyword = ''
  filters.cameraId = undefined
  const t = todayStr()
  filters.dateRange = [t, t]
  loadCameras()
  reload()
}

function cameraLabel(c: any) {
  // 超管看全部门店摄像头时，名称后附带门店 id 便于区分
  return auth.isSuperAdmin && !storeFilter.value && c.store_id ? `${c.name}（门店#${c.store_id}）` : c.name
}

function onStoreChange() {
  // 门店 → 摄像头联动：切店清空已选摄像头并重新拉取该店摄像头
  filters.cameraId = undefined
  loadCameras()
  refreshAll()
}

function reload() {
  hasNewHint.value = false
  page.value = 1
  return load()
}

async function loadStores() {
  try {
    const res = await api.get('/settings/stores/options')
    stores.value = res.data.items || []
  } catch {
    stores.value = []
  }
}

async function loadCameras() {
  try {
    const res = await api.get('/cameras', { params: { store_id: storeFilter.value } })
    cameras.value = res.data?.items || []
  } catch {
    cameras.value = []
  }
}

async function loadStats() {
  try {
    Object.assign(
      stats,
      (await api.get('/welcome/stats', { params: { store_id: storeFilter.value } })).data || {},
    )
  } catch {
    /* */
  }
}

async function load() {
  loading.value = true
  try {
    const params: Record<string, any> = {
      page: page.value,
      page_size: pageSize.value,
      store_id: storeFilter.value,
    }
    if (filters.customerId) params.customer_id = Number(filters.customerId)
    if (filters.keyword?.trim()) params.keyword = filters.keyword.trim()
    if (filters.cameraId) params.camera_id = filters.cameraId
    if (filters.dateRange?.length === 2) {
      params.date_from = filters.dateRange[0]
      params.date_to = filters.dateRange[1]
    }
    const res = await api.get('/welcome/logs', { params })
    logs.value = res.data.items || []
    total.value = res.data.total || 0
  } catch (e: any) {
    ElMessage.error(e.message || '加载失败')
  } finally {
    loading.value = false
  }
}

async function refreshAll() {
  await Promise.all([loadStats(), load()])
}

/** 轮询/WS 自动刷新：仅第 1 页替换列表，非第 1 页只亮提示条并更新 KPI */
async function autoRefreshAll() {
  if (page.value !== 1) {
    hasNewHint.value = true
    await loadStats()
    return
  }
  await refreshAll()
}

async function jumpToLatest() {
  hasNewHint.value = false
  page.value = 1
  await refreshAll()
}

function stopPoll() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

function startPoll() {
  if (pollTimer) return
  pollTimer = setInterval(() => autoRefreshAll(), 10000)
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
  ws = openStaffWebSocket('cameras')
  ws.onopen = () => {
    reconnectAttempt = 0
    connStatus.value = 'online'
    stopPoll()
  }
  ws.onmessage = (ev) => {
    try {
      const msg = JSON.parse(ev.data || '{}')
      if (msg.event === 'WELCOME') {
        ElMessage.success(msg.data?.text || '新迎宾')
      }
    } catch {
      /* */
    }
    autoRefreshAll()
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

onMounted(async () => {
  await loadStores()
  await loadCameras()
  await refreshAll()
  connectWs()
})

onUnmounted(() => {
  stopPoll()
  if (reconnectTimer) clearTimeout(reconnectTimer)
  try {
    ws?.close()
  } catch {
    /* */
  }
})
</script>

<style scoped>
.welcome-page { min-height: 360px; }
.kpi-row { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 14px; align-items: stretch; }
.kpi { min-width: 110px; padding: 12px 14px; background: #f7f8fa; border-radius: 8px; }
.kpi-val { font-size: 22px; font-weight: 700; }
.kpi-label { font-size: 12px; color: #909399; margin-top: 4px; }
.kpi-actions { margin-left: auto; display: flex; gap: 8px; align-items: center; }
.toolbar { margin-bottom: 12px; }
.new-hint {
  margin-bottom: 10px;
  padding: 8px 14px;
  border-radius: 6px;
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
  font-size: 13px;
  cursor: pointer;
  text-align: center;
}
.new-hint:hover { background: var(--el-color-primary-light-7); }
.sub { font-size: 12px; color: #909399; }
.pager { margin-top: 12px; display: flex; justify-content: flex-end; }
</style>
