<template>
  <div class="orders-page" v-loading="pageLoading">
    <!-- KPI -->
    <div class="kpi-row">
      <div v-for="k in kpiCards" :key="k.key" class="kpi-card"
        :class="[{ active: quickFilter === k.filter, alert: k.alert && Number(k.value) > 0 }, k.tone]"
        @click="applyQuick(k.filter)">
        <div class="kpi-val">{{ k.value }}</div>
        <div class="kpi-label">{{ k.label }}</div>
      </div>
      <div class="kpi-meta">
        <div class="conn" :class="connStatus">{{ connLabel }}</div>
        <el-button size="small" @click="refreshAll">刷新</el-button>
      </div>
    </div>

    <div class="toolbar">
      <el-select v-if="auth.isSuperAdmin" v-model="storeFilter" placeholder="全部门店" clearable style="width: 160px"
        @change="onStoreChange">
        <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
      </el-select>
      <el-tag v-else-if="boundStoreName" type="info">门店：{{ boundStoreName }}</el-tag>
      <el-select v-model="filters.platform" placeholder="平台" clearable style="width: 110px" @change="reloadOrders">
        <el-option label="美团" value="MEITUAN" />
        <el-option label="饿了么" value="ELEME" />
      </el-select>
      <el-select v-model="filters.delivery_status" placeholder="接单状态" clearable style="width: 130px"
        @change="reloadOrders">
        <el-option v-for="(t, v) in statusOptions" :key="v" :label="t" :value="v" />
      </el-select>
      <el-select v-model="filters.refund_status" placeholder="退款" clearable style="width: 110px" @change="reloadOrders">
        <el-option label="无退款" value="NONE" />
        <el-option label="待处理" value="PENDING" />
        <el-option label="已同意" value="APPROVED" />
        <el-option label="已拒绝" value="REJECTED" />
      </el-select>
      <el-input v-model="filters.keyword" clearable placeholder="单号 / 姓名 / 电话 / 地址" style="width: 220px"
        @keyup.enter="reloadOrders" @clear="reloadOrders" />
      <el-input v-model="filters.day_seq" clearable placeholder="流水#" style="width: 90px" @keyup.enter="reloadOrders"
        @clear="reloadOrders" />
      <el-date-picker v-model="filters.dateRange" type="daterange" value-format="YYYY-MM-DD" start-placeholder="开始"
        end-placeholder="结束" style="width: 240px" @change="reloadOrders" />
      <el-checkbox v-model="filters.only_urge" @change="reloadOrders">仅催单</el-checkbox>
      <el-button type="primary" @click="reloadOrders">查询</el-button>
    </div>

    <div v-if="hasNewHint" class="new-hint" @click="jumpToLatest">
      有新订单或状态更新，点击回到第 1 页查看最新
    </div>

    <el-table :data="orders" size="small" stripe highlight-current-row :row-class-name="orderRowClass"
      @row-click="(row: any) => openDetail(row)">
      <el-table-column label="平台" width="72">
        <template #default="{ row }">
          <el-tag size="small" :type="row.platform === 'MEITUAN' ? 'warning' : 'success'" effect="plain">
            {{ platformName(row.platform) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="platform_day_seq" label="#" width="56" />
      <el-table-column prop="store_name" label="门店" min-width="120" show-overflow-tooltip>
        <template #default="{ row }">{{ row.store_name || (row.store_id ? `门店#${row.store_id}` : '—') }}</template>
      </el-table-column>
      <el-table-column prop="order_no" label="本系统单号" min-width="240" show-overflow-tooltip />
      <el-table-column label="金额" width="110">
        <template #default="{ row }">
          <span class="money">¥{{ row.total_amount || '-' }}</span>
          <div v-if="Number(row.discount_amount) > 0" class="discount-tip">会员 -¥{{ row.discount_amount }}</div>
          <div v-if="Number(row.coupon_discount) > 0" class="discount-tip">券 -¥{{ row.coupon_discount }}</div>
          <div v-if="Number(row.voucher_discount) > 0" class="discount-tip">代金 -¥{{ row.voucher_discount }}</div>
        </template>
      </el-table-column>
      <el-table-column label="接单" width="100">
        <template #default="{ row }">
          <el-tag size="small" :type="deliveryStatusTagType(row.delivery_status)">
            {{ label(DELIVERY_STATUS, row.delivery_status) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="物流" width="100">
        <template #default="{ row }">
          {{ label(LOGISTICS_STATUS, row.logistics_status, '—') }}
        </template>
      </el-table-column>
      <el-table-column label="退款" width="80">
        <template #default="{ row }">
          <el-tag v-if="row.refund_status && row.refund_status !== 'NONE'" size="small"
            :type="row.refund_status === 'PENDING' ? 'danger' : 'info'">
            {{ label(DELIVERY_REFUND_STATUS, row.refund_status) }}
          </el-tag>
          <span v-else class="muted">-</span>
        </template>
      </el-table-column>
      <el-table-column label="催" width="50">
        <template #default="{ row }">
          <el-badge v-if="row.urge_count > 0" :value="row.urge_count" type="danger" />
          <span v-else class="muted">0</span>
        </template>
      </el-table-column>
      <el-table-column prop="recipient_name" label="顾客" width="72" show-overflow-tooltip />
      <el-table-column prop="recipient_phone" label="隐私号" width="120" show-overflow-tooltip />
      <el-table-column prop="recipient_address" label="地址" min-width="160" show-overflow-tooltip />
      <el-table-column label="骑手" width="100" show-overflow-tooltip>
        <template #default="{ row }">
          <span v-if="row.rider_name">{{ row.rider_name }}</span>
          <span v-else class="muted">-</span>
        </template>
      </el-table-column>
      <el-table-column label="下单" width="150">
        <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="200" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" size="small" @click.stop="openDetail(row)">详情</el-button>
          <el-button
            v-if="canOperateDelivery && row.delivery_status === 'PENDING'"
            link
            type="success"
            size="small"
            :loading="actingId === row.order_id"
            @click.stop="confirmOrder(row)"
          >
            接单
          </el-button>
          <el-button
            v-if="canOperateDelivery && canCancel(row)"
            link
            type="danger"
            size="small"
            @click.stop="askCancel(row)"
          >
            取消
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <div class="pager">
      <el-pagination v-model:current-page="pager.page" v-model:page-size="pager.pageSize" :total="pager.total"
        :page-sizes="[20, 50, 100]" layout="total, sizes, prev, pager, next" background small
        @current-change="loadOrders" @size-change="reloadOrders" />
    </div>

    <!-- 详情抽屉 -->
    <el-drawer v-model="detailVisible" title="外卖订单详情" size="480px" destroy-on-close>
      <div v-loading="detailLoading" class="detail">
        <template v-if="detail">
          <div class="detail-head">
            <el-tag :type="detail.platform === 'MEITUAN' ? 'warning' : 'success'">{{ platformName(detail.platform)
            }}</el-tag>
            <span class="seq">#{{ detail.platform_day_seq || '-' }}</span>
            <el-tag :type="deliveryStatusTagType(detail.delivery_status)" size="small">
              {{ label(DELIVERY_STATUS, detail.delivery_status) }}
            </el-tag>
            <el-tag v-if="detail.urge_count > 0" type="danger" size="small">催×{{ detail.urge_count }}</el-tag>
          </div>
          <el-descriptions :column="1" border size="small" class="mt">
            <el-descriptions-item label="平台单号">{{ detail.platform_order_id }}</el-descriptions-item>
            <el-descriptions-item label="本系统单号">{{ detail.order_no }}</el-descriptions-item>
            <el-descriptions-item label="餐品小计">¥{{ detail.subtotal || '-' }}</el-descriptions-item>
            <el-descriptions-item label="会员优惠">
              <span v-if="Number(detail.discount_amount) > 0" class="discount-text">-¥{{ detail.discount_amount
              }}</span>
              <span v-else class="muted">—</span>
            </el-descriptions-item>
            <el-descriptions-item label="优惠券">
              <span v-if="Number(detail.coupon_discount) > 0" class="discount-text">-¥{{ detail.coupon_discount
              }}</span>
              <span v-else class="muted">—</span>
            </el-descriptions-item>
            <el-descriptions-item label="代金券">
              <span v-if="Number(detail.voucher_discount) > 0" class="discount-text">-¥{{ detail.voucher_discount
              }}</span>
              <span v-else class="muted">—</span>
            </el-descriptions-item>
            <el-descriptions-item label="应付金额"><span class="money">¥{{ detail.total_amount
            }}</span></el-descriptions-item>
            <el-descriptions-item label="配送费">¥{{ detail.delivery_fee }}</el-descriptions-item>
            <el-descriptions-item label="餐盒费">¥{{ detail.packing_fee }}</el-descriptions-item>
            <el-descriptions-item label="物流">
              {{ label(LOGISTICS_STATUS, detail.logistics_status, '—') }}
              <span v-if="detail.logistics_code" class="muted"> ({{ detail.logistics_code }})</span>
            </el-descriptions-item>
            <el-descriptions-item label="顾客">{{ detail.recipient_name }} {{ detail.recipient_phone
            }}</el-descriptions-item>
            <el-descriptions-item label="地址">{{ detail.recipient_address || '-' }}</el-descriptions-item>
            <el-descriptions-item label="骑手">
              {{ detail.rider_name || '-' }}
              <span v-if="detail.rider_phone"> {{ detail.rider_phone }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="期望送达">{{ fmtTime(detail.expected_delivery_at) }}</el-descriptions-item>
            <el-descriptions-item label="备注">{{ detail.remark || '-' }}</el-descriptions-item>
            <el-descriptions-item label="下单时间">{{ fmtTime(detail.created_at) }}</el-descriptions-item>
          </el-descriptions>

          <h4 class="mt">菜品</h4>
          <div v-for="it in detail.items || []" :key="it.id" class="item-row">
            <div class="item-name">
              {{ it.product_name }} ×{{ it.quantity }}
              <span v-if="it.remark" class="item-remark">（{{ it.remark }}）</span>
            </div>
            <div class="item-opts" v-if="it.options?.length">
              <span v-for="(o, i) in it.options" :key="i">{{ o.group_name }}:{{ o.option_name }} </span>
            </div>
            <div class="item-price">¥{{ it.subtotal }}</div>
          </div>
          <div v-if="!(detail.items || []).length" class="muted">暂无明细</div>

          <div class="detail-actions mt">
            <el-button
              v-if="canOperateDelivery && detail.delivery_status === 'PENDING'"
              type="success"
              :loading="actingId === detail.order_id"
              @click="confirmOrder(detail)"
            >
              接单
            </el-button>
            <el-button
              v-if="canOperateDelivery && canCancel(detail)"
              type="danger"
              plain
              @click="askCancel(detail)"
            >商家取消</el-button>
          </div>
        </template>
      </div>
    </el-drawer>

    <!-- 取消原因 -->
    <el-dialog v-model="cancelVisible" title="商家取消外卖单" width="420px">
      <el-form label-width="80px">
        <el-form-item label="取消原因" required>
          <el-select v-model="cancelReason" filterable allow-create default-first-option style="width: 100%">
            <el-option v-for="r in cancelReasons" :key="r" :label="r" :value="r" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="cancelVisible = false">关闭</el-button>
        <el-button type="danger" :loading="cancelLoading" @click="doCancel">确认取消</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '@/api'
import { useStoreOptions } from '@/composables/useStoreOptions'
import { openStaffWebSocket } from '@/utils/ws'
import { useAuthStore } from '@/stores/auth'
import {
  DELIVERY_REFUND_STATUS,
  DELIVERY_STATUS,
  LOGISTICS_STATUS,
  deliveryStatusTagType,
  label,
} from '@/utils/labels'

const router = useRouter()
const auth = useAuthStore()
const canOperateDelivery = computed(
  () => auth.isSuperAdmin || auth.hasPermission('order:update'),
)
const { storeOptions: stores, loadStores } = useStoreOptions()
const storeFilter = ref<number | undefined>()
const boundStoreName = computed(() => {
  const sid = auth.user?.store_id
  if (!sid) return ''
  return stores.value.find((s) => s.id === sid)?.name || `门店#${sid}`
})

const pageLoading = ref(false)
const orders = ref<any[]>([])
const stats = reactive({
  pending: 0,
  accepted: 0,
  delivering: 0,
  urging: 0,
  refund_pending: 0,
  orders: 0,
  meituan_orders: 0,
  eleme_orders: 0,
  revenue: '0',
  member_discount: '0',
})

const filters = reactive({
  platform: '' as string,
  delivery_status: '' as string,
  refund_status: '' as string,
  keyword: '',
  day_seq: '',
  only_urge: false,
  dateRange: null as string[] | null,
})
const quickFilter = ref('')
const pager = reactive({ page: 1, pageSize: 20, total: 0 })
// 非第1页/详情打开期间收到实时事件，不直接替换列表，仅提示（防止翻页时行跳动、新单无感）
const hasNewHint = ref(false)

const statusOptions = {
  PENDING: '待接单',
  ACCEPTED: '已接单',
  READY: '待配送',
  DELIVERING: '配送中',
  DONE: '已完成',
  CANCELLED: '已取消',
}

const kpiCards = computed(() => [
  { key: 'pending', label: '待接单', value: stats.pending, filter: 'pending', alert: true, tone: 'tone-danger' },
  { key: 'urging', label: '催单中', value: stats.urging, filter: 'urge', alert: true, tone: 'tone-warn' },
  { key: 'refund', label: '待退款', value: stats.refund_pending, filter: 'refund', alert: true, tone: 'tone-warn' },
  { key: 'accepted', label: '制作/待送', value: stats.accepted, filter: 'accepted', alert: false, tone: '' },
  { key: 'today', label: '今日单量', value: stats.orders, filter: 'today', alert: false, tone: '' },
  {
    key: 'rev',
    label: '今日外卖净营收',
    value: `¥${stats.revenue}`,
    filter: '',
    alert: false,
    tone: 'tone-ok',
  },
  {
    key: 'discount',
    label: '今日会员优惠',
    value: Number(stats.member_discount) > 0 ? `¥${stats.member_discount}` : '¥0',
    filter: '',
    alert: false,
    tone: 'tone-discount',
  },
  {
    key: 'plat',
    label: '美团 / 饿了么',
    value: `${stats.meituan_orders} / ${stats.eleme_orders}`,
    filter: '',
    alert: false,
    tone: '',
  },
])

const connStatus = ref<'online' | 'polling' | 'offline' | 'connecting'>('connecting')
const connLabel = computed(() => {
  const m = { online: '实时在线', polling: '轮询中', offline: '已离线', connecting: '连接中…' }
  return m[connStatus.value]
})

const actingId = ref<number | null>(null)
const detailVisible = ref(false)
const detailLoading = ref(false)
const detail = ref<any>(null)

const cancelVisible = ref(false)
const cancelLoading = ref(false)
const cancelReason = ref('商家忙碌，无法接单')
const cancelTarget = ref<any>(null)
const cancelReasons = ['商家忙碌，无法接单', '餐品售罄', '超出配送范围', '顾客要求取消', '其他原因']

let ws: WebSocket | null = null
let pollTimer: ReturnType<typeof setInterval> | null = null
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
let reconnectAttempt = 0

function platformName(p: string) {
  return p === 'MEITUAN' ? '美团' : p === 'ELEME' ? '饿了么' : p || '-'
}

function fmtTime(v?: string | null) {
  if (!v) return '-'
  return String(v).replace('T', ' ').slice(0, 19)
}

function canCancel(row: any) {
  return row && !['DONE', 'COMPLETED', 'CANCELLED'].includes(row.delivery_status)
}

function orderRowClass({ row }: { row: any }) {
  if (row.delivery_status === 'PENDING') return 'row-pending'
  if (row.urge_count > 0) return 'row-urge'
  if (row.refund_status === 'PENDING') return 'row-refund'
  return ''
}

function applyQuick(filter: string) {
  if (!filter) return
  quickFilter.value = filter
  filters.only_urge = false
  filters.delivery_status = ''
  filters.refund_status = ''
  filters.dateRange = null
  if (filter === 'pending') filters.delivery_status = 'PENDING'
  else if (filter === 'accepted') filters.delivery_status = 'ACCEPTED'
  else if (filter === 'urge') filters.only_urge = true
  else if (filter === 'refund') {
    router.push('/delivery/refunds')
    return
  } else if (filter === 'today') {
    const d = new Date()
    const s = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
    filters.dateRange = [s, s]
  }
  reloadOrders()
}

function onStoreChange() {
  // 门店变更：筛选条件变化回到第 1 页（项目约定），避免停留在越界页码
  pager.page = 1
  refreshAll()
}

async function loadStats() {
  try {
    const res = await api.get('/delivery/stats', {
      params: { store_id: storeFilter.value },
    })
    Object.assign(stats, res.data || {})
  } catch {
    /* */
  }
}

async function loadOrders() {
  const params: Record<string, any> = {
    page: pager.page,
    page_size: pager.pageSize,
    store_id: storeFilter.value,
  }
  if (filters.platform) params.platform = filters.platform
  if (filters.delivery_status) params.delivery_status = filters.delivery_status
  if (filters.refund_status) params.refund_status = filters.refund_status
  if (filters.keyword?.trim()) params.keyword = filters.keyword.trim()
  if (filters.day_seq?.trim()) params.day_seq = filters.day_seq.trim()
  if (filters.only_urge) params.only_urge = true
  if (filters.dateRange?.length === 2) {
    params.date_from = filters.dateRange[0]
    params.date_to = filters.dateRange[1]
  }
  let res
  try {
    res = await api.get('/delivery/orders', { params })
  } catch (e: any) {
    // 轮询/WS 自动刷新的网络抖动不应每 8 秒抛一次未处理 rejection
    if (!pageLoading.value) ElMessage.error(e?.message || '加载外卖订单失败')
    return
  }
  const items = res.data?.items || []
  // 操作（接单/取消）或数据变化导致当前页为空时，自动回退到上一页，避免停在空白页
  if (items.length === 0 && pager.page > 1) {
    pager.page -= 1
    return loadOrders()
  }
  orders.value = items
  pager.total = res.data?.total || 0
}

function reloadOrders() {
  hasNewHint.value = false
  pager.page = 1
  return loadOrders()
}

/** 轮询/WebSocket 实时刷新：仅第 1 页且未开详情时自动替换列表，否则只亮提示条 */
async function autoReloadOrders() {
  if (pager.page !== 1 || detailVisible.value) {
    hasNewHint.value = true
    return
  }
  await loadOrders()
}

/** 点击"有新订单"提示条：回第 1 页看最新数据 */
async function jumpToLatest() {
  hasNewHint.value = false
  pager.page = 1
  await Promise.all([loadOrders(), loadStats()])
}

async function refreshAll() {
  pageLoading.value = true
  try {
    await Promise.all([loadStats(), loadOrders()])
  } finally {
    pageLoading.value = false
  }
}

async function openDetail(row: any) {
  detailVisible.value = true
  detailLoading.value = true
  detail.value = null
  try {
    const res = await api.get(`/delivery/orders/${row.id}`)
    detail.value = res.data
  } catch (e: any) {
    ElMessage.error(e.message || '加载详情失败')
    detailVisible.value = false
  } finally {
    detailLoading.value = false
  }
}

async function confirmOrder(row: any) {
  actingId.value = row.order_id
  try {
    await api.post(`/delivery/orders/${row.order_id}/confirm`)
    ElMessage.success('已接单')
    await Promise.all([loadOrders(), loadStats()])
    if (detailVisible.value && detail.value?.order_id === row.order_id) {
      await openDetail({ id: detail.value.id })
    }
  } catch (e: any) {
    ElMessage.error(e.message || '接单失败')
  } finally {
    actingId.value = null
  }
}

function askCancel(row: any) {
  cancelTarget.value = row
  cancelReason.value = '商家忙碌，无法接单'
  cancelVisible.value = true
}

async function doCancel() {
  if (!cancelTarget.value) return
  if (!cancelReason.value?.trim()) {
    ElMessage.warning('请填写取消原因')
    return
  }
  cancelLoading.value = true
  try {
    await api.post(`/delivery/orders/${cancelTarget.value.id}/cancel`, { reason: cancelReason.value.trim() })
    ElMessage.success('已取消')
    cancelVisible.value = false
    detailVisible.value = false
    await Promise.all([loadOrders(), loadStats()])
  } catch (e: any) {
    ElMessage.error(e.message || '取消失败')
  } finally {
    cancelLoading.value = false
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
  pollTimer = setInterval(() => {
    loadStats()
    autoReloadOrders()
  }, 8000)
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
  ws = openStaffWebSocket('orders')
  ws.onopen = () => {
    reconnectAttempt = 0
    connStatus.value = 'online'
    stopPoll()
  }
  ws.onmessage = (ev) => {
    try {
      const msg = JSON.parse(ev.data || '{}')
      const event = msg.event || ''
      if (event === 'ORDER_URGE') ElMessage.warning('收到催单')
      else if (event === 'ORDER_REFUND') ElMessage.warning('收到退款申请')
      else if (event === 'ORDER_NEW') ElMessage.info('新外卖订单')
    } catch {
      /* */
    }
    loadStats()
    autoReloadOrders()
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

watch(
  () => filters.only_urge,
  (v) => {
    if (v) quickFilter.value = 'urge'
  },
)

onMounted(async () => {
  pageLoading.value = true
  try {
    // 门店下拉与列表/统计无强依赖，并行缩短首屏
    await Promise.all([loadStores(), loadStats(), loadOrders()])
  } finally {
    pageLoading.value = false
  }
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
.orders-page {
  min-height: 400px;
}

.kpi-row {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 14px;
  align-items: stretch;
}

.kpi-card {
  min-width: 108px;
  padding: 12px 14px;
  background: rgba(26, 50, 92, 0.62);
  border-radius: 12px;
  cursor: pointer;
  border: 1px solid rgba(120, 200, 255, 0.28);
  box-shadow: 0 0 16px rgba(80, 165, 245, 0.14);
  transition: border-color 0.15s, background 0.15s, box-shadow 0.15s;
}

.kpi-card:hover {
  border-color: rgba(127, 200, 255, 0.55);
  box-shadow: 0 0 22px rgba(80, 165, 245, 0.28);
}

.kpi-card.active {
  border-color: #7fc8ff;
  background: rgba(80, 165, 245, 0.22);
  box-shadow: 0 0 22px rgba(91, 168, 245, 0.4);
}

.kpi-card.alert {
  animation: pulse 1.6s ease-in-out infinite;
}

.kpi-card.tone-danger {
  background: rgba(200, 50, 50, 0.2);
  border-color: rgba(245, 108, 108, 0.45);
}

.kpi-card.tone-warn {
  background: rgba(200, 120, 30, 0.18);
  border-color: rgba(230, 162, 60, 0.45);
}

.kpi-card.tone-ok {
  background: rgba(52, 201, 123, 0.14);
  border-color: rgba(110, 231, 167, 0.4);
}

.kpi-card.tone-discount {
  background: rgba(200, 50, 50, 0.18);
  border-color: rgba(245, 108, 108, 0.4);
}

.kpi-val {
  font-size: 22px;
  font-weight: 700;
  color: #7fc8ff;
  text-shadow: 0 0 10px rgba(127, 200, 255, 0.4);
  line-height: 1.2;
}

.kpi-label {
  font-size: 12px;
  color: #7a9cc4;
  margin-top: 4px;
}

.kpi-meta {
  margin-left: auto;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 6px;
  justify-content: center;
}

.conn {
  font-size: 12px;
  padding: 2px 8px;
  border-radius: 4px;
  background: rgba(30, 58, 102, 0.7);
  color: #7a9cc4;
  border: 1px solid rgba(120, 200, 255, 0.22);
}

.conn.online {
  background: rgba(52, 201, 123, 0.16);
  border-color: rgba(110, 231, 167, 0.4);
  color: #6ee7a7;
}

.conn.polling {
  background: rgba(200, 120, 30, 0.18);
  border-color: rgba(230, 162, 60, 0.45);
  color: #f5c87c;
}

.conn.offline,
.conn.connecting {
  background: rgba(30, 58, 102, 0.7);
  color: #7a9cc4;
}

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

.new-hint:hover {
  background: var(--el-color-primary-light-7);
}

.toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin-bottom: 12px;
}

.pager {
  margin-top: 12px;
  display: flex;
  justify-content: flex-end;
}

.money {
  font-weight: 600;
  color: #e6a23c;
}

.discount-tip {
  font-size: 11px;
  color: #f56c6c;
  line-height: 1.2;
}

.discount-text {
  color: #f56c6c;
  font-weight: 600;
}

.muted {
  color: #c0c4cc;
}

.detail-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.seq {
  font-size: 20px;
  font-weight: 700;
}

.mt {
  margin-top: 16px;
}

.item-row {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 4px 12px;
  padding: 8px 0;
  border-bottom: 1px solid #f0f0f0;
}

.item-name {
  font-weight: 500;
}

.item-remark {
  color: #e6a23c;
  font-size: 12px;
}

.item-opts {
  grid-column: 1 / -1;
  font-size: 12px;
  color: #909399;
}

.item-price {
  color: #606266;
}

.detail-actions {
  display: flex;
  gap: 8px;
}

:deep(.row-pending) {
  background: #fef0f0 !important;
}

:deep(.row-urge) {
  background: #fdf6ec !important;
}

:deep(.row-refund) {
  background: #fdf6ec !important;
}

@keyframes pulse {

  0%,
  100% {
    box-shadow: 0 0 0 0 rgba(245, 108, 108, 0.25);
  }

  50% {
    box-shadow: 0 0 0 4px rgba(245, 108, 108, 0.08);
  }
}
</style>
