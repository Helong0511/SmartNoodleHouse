<template>
  <div v-loading="loading">
    <el-form inline class="toolbar">
      <el-form-item v-if="auth.isSuperAdmin">
        <el-select v-model="storeFilter" placeholder="全部门店" clearable style="width:160px" @change="reload">
          <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
        </el-select>
      </el-form-item>
      <el-tag v-else-if="boundStoreName" type="info" style="margin-right:12px">门店：{{ boundStoreName }}</el-tag>
      <el-form-item>
        <el-select v-model="statusFilter" placeholder="状态" clearable style="width:130px" @change="reload">
          <el-option v-for="(lbl, key) in STATUS_LABELS" :key="key" :label="lbl" :value="key" />
        </el-select>
      </el-form-item>
      <el-button type="primary" @click="reload">查询</el-button>
      <el-button @click="resetFilters">重置</el-button>
      <el-button type="success" @click="openCreate">为顾客代开票</el-button>
    </el-form>

    <el-table :data="items" border>
      <el-table-column prop="created_at" label="申请时间" width="170" />
      <el-table-column prop="order_no" label="订单号" min-width="220" show-overflow-tooltip />
      <el-table-column label="门店" min-width="110" show-overflow-tooltip>
        <template #default="{ row }">{{ row.store_name || (row.store_id ? `门店#${row.store_id}` : '—') }}</template>
      </el-table-column>
      <el-table-column prop="title" label="发票抬头" min-width="180" show-overflow-tooltip />
      <el-table-column label="类型" min-width="200" show-overflow-tooltip>
        <template #default="{ row }">
          <template v-if="row.title_type === 'COMPANY'">
            单位<span v-if="row.tax_no">（税号：{{ row.tax_no }}）</span>
          </template>
          <span v-else>个人</span>
        </template>
      </el-table-column>
      <el-table-column prop="email" label="邮箱" min-width="170" show-overflow-tooltip />
      <el-table-column label="金额" width="110">
        <template #default="{ row }">¥{{ formatAmount(row.amount) }}</template>
      </el-table-column>
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag size="small" :type="STATUS_TAG_TYPES[row.status] || 'info'">
            {{ STATUS_LABELS[row.status] || '—' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="开票时间" width="170">
        <template #default="{ row }">{{ row.issued_at || '—' }}</template>
      </el-table-column>
      <el-table-column label="驳回原因" min-width="150" show-overflow-tooltip>
        <template #default="{ row }">{{ row.reject_reason || '—' }}</template>
      </el-table-column>
      <el-table-column label="操作" width="170" fixed="right">
        <template #default="{ row }">
          <template v-if="row.status === 'PENDING'">
            <el-button
              link
              type="success"
              :loading="busyId === rowRequestId(row)"
              @click="markIssued(row)"
            >标记已开票</el-button>
            <el-button link type="danger" @click="reject(row)">驳回</el-button>
          </template>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>
    </el-table>

    <el-pagination
      v-model:current-page="page"
      v-model:page-size="pageSize"
      :total="total"
      :page-sizes="[20, 50, 100]"
      layout="total, sizes, prev, pager, next"
      style="margin-top:16px"
      @current-change="load"
      @size-change="reload"
    />

    <el-dialog v-model="createVisible" title="为顾客代开票" width="640px" destroy-on-close @closed="resetCreateForm">
      <p class="create-hint">先选顾客，再选其已支付且未开票的订单；有历史抬头会自动带入，可修改。</p>
      <el-form :model="createForm" label-width="96px">
        <el-form-item label="选择顾客" required>
          <el-select
            v-model="selectedCustomerId"
            filterable
            remote
            clearable
            reserve-keyword
            placeholder="搜索手机号 / 姓名 / 会员号"
            :remote-method="searchCustomers"
            :loading="customerLoading"
            style="width:100%"
            @change="onCustomerPicked"
            @visible-change="(v: boolean) => v && searchCustomers('')"
          >
            <el-option
              v-for="c in customerOptions"
              :key="c.customer_id"
              :label="customerOptionLabel(c)"
              :value="c.customer_id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="选择订单" required>
          <div class="order-toolbar">
            <el-select
              v-model="createForm.order_no"
              filterable
              clearable
              :disabled="selectedCustomerId === null || selectedCustomerId === undefined"
              :placeholder="selectedCustomerId == null ? '请先选择顾客' : '选择订单（已开票/开票中不显示）'"
              :loading="orderLoading"
              style="flex:1"
              @change="onOrderPicked"
            >
              <el-option
                v-for="o in orderOptions"
                :key="o.order_no"
                :label="orderOptionLabel(o)"
                :value="o.order_no"
              />
            </el-select>
            <el-select v-model="orderSort" style="width:130px" :disabled="selectedCustomerId == null" @change="loadOrdersForCustomer">
              <el-option label="日期近→远" value="date_desc" />
              <el-option label="日期远→近" value="date_asc" />
              <el-option label="金额高→低" value="amount_desc" />
              <el-option label="金额低→高" value="amount_asc" />
              <el-option label="订单号 A→Z" value="order_no_asc" />
              <el-option label="订单号 Z→A" value="order_no_desc" />
            </el-select>
          </div>
          <el-table
            v-if="orderOptions.length"
            :data="orderOptions"
            size="small"
            border
            highlight-current-row
            class="order-pick-table"
            :row-class-name="orderRowClass"
            @row-click="onOrderRowClick"
          >
            <el-table-column prop="order_no" label="订单号" min-width="200" show-overflow-tooltip />
            <el-table-column label="日期" width="150">
              <template #default="{ row }">{{ formatOrderTime(row.created_at) }}</template>
            </el-table-column>
            <el-table-column label="金额" width="100" align="right">
              <template #default="{ row }">
                <span class="order-amt">¥{{ formatAmount(row.amount) }}</span>
              </template>
            </el-table-column>
          </el-table>
          <p v-else-if="selectedCustomerId != null && !orderLoading" class="order-empty">该顾客暂无可开票订单</p>
        </el-form-item>
        <el-alert
          v-if="profileHint"
          :title="profileHint"
          type="success"
          :closable="false"
          show-icon
          style="margin-bottom:14px"
        />
        <el-form-item label="抬头类型" required>
          <el-radio-group v-model="createForm.title_type">
            <el-radio value="PERSON">个人</el-radio>
            <el-radio value="COMPANY">单位</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="抬头名称" required>
          <el-input v-model="createForm.title" placeholder="发票抬头" maxlength="200" clearable />
        </el-form-item>
        <el-form-item v-if="createForm.title_type === 'COMPANY'" label="单位税号" required>
          <el-input v-model="createForm.tax_no" placeholder="18 位统一社会信用代码" maxlength="50" clearable />
        </el-form-item>
        <el-form-item label="接收邮箱" required>
          <el-input v-model="createForm.email" placeholder="用于发送电子发票" maxlength="100" clearable />
        </el-form-item>
        <el-form-item label="直接开票">
          <el-switch v-model="createForm.mark_issued" />
          <span class="switch-hint">开启后创建即为「已开票」，无需再点标记</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="submitCreate">提交</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api'
import { useStoreOptions } from '@/composables/useStoreOptions'
import { useAuthStore } from '@/stores/auth'
import { normalizeTaxNo, taxNoErrorMessage } from '@/utils/taxNo'

const STATUS_LABELS: Record<string, string> = {
  PENDING: '待开票',
  ISSUED: '已开票',
  REJECTED: '已驳回',
}
const STATUS_TAG_TYPES: Record<string, string> = {
  PENDING: 'warning',
  ISSUED: 'success',
  REJECTED: 'info',
}
const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/

type InvoiceProfile = {
  title_type?: string
  title?: string
  tax_no?: string | null
  email?: string
  source_order_no?: string
  source_status?: string
} | null

type CustomerCandidate = {
  customer_id: number
  customer_no?: string | null
  customer_name?: string | null
  customer_phone?: string | null
  eligible_order_count?: number
  has_profile?: boolean
  last_profile?: InvoiceProfile
}

type OrderCandidate = {
  order_id: number
  order_no: string
  amount: string
  created_at?: string
  customer_id?: number | null
  customer_name?: string | null
  customer_phone?: string | null
  store_name?: string | null
  has_profile?: boolean
  last_profile?: InvoiceProfile
}

const auth = useAuthStore()
const route = useRoute()
const loading = ref(false)
const items = ref<any[]>([])
const { storeOptions: stores, loadStores } = useStoreOptions()
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const storeFilter = ref<number | undefined>()
const statusFilter = ref<string | undefined>()
const busyId = ref<number | null>(null)

const createVisible = ref(false)
const creating = ref(false)
const customerLoading = ref(false)
const orderLoading = ref(false)
const customerOptions = ref<CustomerCandidate[]>([])
const orderOptions = ref<OrderCandidate[]>([])
const selectedCustomerId = ref<number | null>(null)
const orderSort = ref('date_desc')
const profileHint = ref('')
const createForm = reactive({
  order_no: '',
  title_type: 'PERSON',
  title: '',
  tax_no: '',
  email: '',
  mark_issued: false,
})

const boundStoreName = computed(() => {
  const sid = auth.user?.store_id
  if (!sid) return ''
  return stores.value.find((s) => s.id === sid)?.name || `门店#${sid}`
})

function rowRequestId(row: any): number {
  return Number(row?.request_id ?? row?.id ?? 0) || 0
}

function reload() {
  page.value = 1
  load()
}

function resetFilters() {
  storeFilter.value = undefined
  statusFilter.value = undefined
  reload()
}

function openCreate() {
  resetCreateForm()
  createVisible.value = true
  searchCustomers('')
}

function resetCreateForm() {
  createForm.order_no = ''
  createForm.title_type = 'PERSON'
  createForm.title = ''
  createForm.tax_no = ''
  createForm.email = ''
  createForm.mark_issued = false
  profileHint.value = ''
  selectedCustomerId.value = null
  orderSort.value = 'date_desc'
  customerOptions.value = []
  orderOptions.value = []
}

function formatAmount(v: any) {
  const n = Number(v)
  return Number.isFinite(n) ? n.toFixed(2) : '0.00'
}

function formatOrderTime(v?: string) {
  if (!v) return '—'
  return String(v).replace('T', ' ').slice(0, 16)
}

function customerOptionLabel(c: CustomerCandidate) {
  if (c.customer_id === 0) return `未绑定顾客（${c.eligible_order_count || 0} 单可开票）`
  const name = c.customer_name || '顾客'
  const phone = c.customer_phone ? ` ${c.customer_phone}` : ''
  const cnt = c.eligible_order_count != null ? ` · ${c.eligible_order_count} 单可开票` : ''
  const tag = c.has_profile ? ' · 有历史抬头' : ''
  return `${name}${phone}${cnt}${tag}`
}

function orderOptionLabel(o: OrderCandidate) {
  return `${formatOrderTime(o.created_at)} · ¥${formatAmount(o.amount)} · ${o.order_no}`
}

function orderRowClass({ row }: { row: OrderCandidate }) {
  return row.order_no === createForm.order_no ? 'is-picked' : ''
}

function applyProfile(profile: InvoiceProfile) {
  if (!profile) {
    profileHint.value = ''
    return
  }
  const tt = String(profile.title_type || '').toUpperCase()
  createForm.title_type = tt === 'COMPANY' ? 'COMPANY' : 'PERSON'
  createForm.title = String(profile.title || '')
  createForm.tax_no = String(profile.tax_no || '')
  createForm.email = String(profile.email || '')
  const from = profile.source_order_no ? `（来自订单 ${profile.source_order_no}）` : ''
  profileHint.value = `已填入该顾客上次开票信息${from}，可直接修改`
}

async function searchCustomers(keyword: string) {
  customerLoading.value = true
  try {
    const params: Record<string, unknown> = {
      page: 1,
      page_size: 30,
      keyword: (keyword || '').trim() || undefined,
      store_id: auth.isSuperAdmin ? storeFilter.value : undefined,
    }
    const res: any = await api.get('/admin/invoices/customer-candidates', { params })
    const body = res?.data ?? res ?? {}
    customerOptions.value = Array.isArray(body.items) ? body.items : []
  } catch (e: any) {
    customerOptions.value = []
    ElMessage.error(e?.message || '加载顾客失败')
  } finally {
    customerLoading.value = false
  }
}

async function loadOrdersForCustomer() {
  if (selectedCustomerId.value == null) {
    orderOptions.value = []
    return
  }
  orderLoading.value = true
  try {
    const params: Record<string, unknown> = {
      page: 1,
      page_size: 50,
      customer_id: selectedCustomerId.value,
      sort: orderSort.value,
      store_id: auth.isSuperAdmin ? storeFilter.value : undefined,
    }
    const res: any = await api.get('/admin/invoices/order-candidates', { params })
    const body = res?.data ?? res ?? {}
    orderOptions.value = Array.isArray(body.items) ? body.items : []
  } catch (e: any) {
    orderOptions.value = []
    ElMessage.error(e?.message || '加载订单失败')
  } finally {
    orderLoading.value = false
  }
}

async function onCustomerPicked(customerId: number | null | undefined) {
  createForm.order_no = ''
  orderOptions.value = []
  if (customerId === null || customerId === undefined) {
    selectedCustomerId.value = null
    profileHint.value = ''
    return
  }
  selectedCustomerId.value = customerId
  const hit = customerOptions.value.find((c) => c.customer_id === customerId)
  if (hit?.last_profile) applyProfile(hit.last_profile)
  else if (customerId === 0) profileHint.value = ''
  else profileHint.value = '该顾客暂无历史开票信息，请手工填写'
  await loadOrdersForCustomer()
}

function onOrderRowClick(row: OrderCandidate) {
  createForm.order_no = row.order_no
  onOrderPicked(row.order_no)
}

function onOrderPicked(orderNo: string) {
  if (!orderNo) return
  const hit = orderOptions.value.find((o) => o.order_no === orderNo)
  // 顾客级历史抬头已在选顾客时带入；订单侧若有则覆盖
  if (hit?.last_profile) applyProfile(hit.last_profile)
}

async function submitCreate() {
  const orderNo = createForm.order_no.trim()
  const title = createForm.title.trim()
  const email = createForm.email.trim()
  const taxNo = normalizeTaxNo(createForm.tax_no)
  createForm.tax_no = taxNo
  if (!orderNo) {
    ElMessage.warning('请选择订单')
    return
  }
  if (!title) {
    ElMessage.warning('请填写发票抬头')
    return
  }
  if (createForm.title_type === 'COMPANY') {
    const taxErr = taxNoErrorMessage(taxNo)
    if (taxErr) {
      ElMessage.warning(taxErr)
      return
    }
  }
  if (!EMAIL_RE.test(email)) {
    ElMessage.warning('邮箱格式不正确')
    return
  }
  creating.value = true
  try {
    const payload: Record<string, unknown> = {
      order_no: orderNo,
      title_type: createForm.title_type,
      title,
      email,
      mark_issued: !!createForm.mark_issued,
    }
    if (createForm.title_type === 'COMPANY') payload.tax_no = taxNo
    await api.post('/admin/invoices', payload)
    ElMessage.success(createForm.mark_issued ? '已为顾客开票' : '已创建开票申请')
    createVisible.value = false
    await load()
  } catch (e: any) {
    ElMessage.error(e?.message || '代开票失败')
  } finally {
    creating.value = false
  }
}

async function load() {
  loading.value = true
  try {
    const params: Record<string, unknown> = {
      page: page.value,
      page_size: pageSize.value,
      status: statusFilter.value,
      store_id: auth.isSuperAdmin ? storeFilter.value : undefined,
    }
    const res: any = await api.get('/admin/invoices', { params })
    const body = res?.data ?? res ?? {}
    const list = Array.isArray(body) ? body : body.items || []
    if (list.length === 0 && page.value > 1) {
      page.value -= 1
      loading.value = false
      await load()
      return
    }
    items.value = list
    total.value = Number(Array.isArray(body) ? list.length : body.total ?? list.length) || 0
  } catch (e: any) {
    ElMessage.error(e?.message || '加载发票申请失败')
  } finally {
    loading.value = false
  }
}

async function markIssued(row: any) {
  const rid = rowRequestId(row)
  if (!rid) {
    ElMessage.error('缺少申请编号')
    return
  }
  try {
    await ElMessageBox.confirm(
      `确认为订单 ${row.order_no || `#${row.order_id}`} 标记已开票？开票结果请自行发送至 ${row.email || '顾客邮箱'}`,
      '标记已开票',
      { confirmButtonText: '确认开票', cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return
  }
  busyId.value = rid
  try {
    await api.post(`/admin/invoices/${rid}/issue`)
    ElMessage.success('已标记开票')
    await load()
  } catch (e: any) {
    ElMessage.error(e.message || '操作失败')
  } finally {
    busyId.value = null
  }
}

async function reject(row: any) {
  const rid = rowRequestId(row)
  if (!rid) {
    ElMessage.error('缺少申请编号')
    return
  }
  let reason = ''
  try {
    const res = await ElMessageBox.prompt('请填写驳回原因（必填）', '驳回开票申请', {
      confirmButtonText: '确认驳回',
      cancelButtonText: '取消',
      inputPlaceholder: '如：订单已退款，无法开票',
      inputValidator: (v: string) => (v && v.trim() ? true : '驳回原因不能为空'),
    })
    reason = (res.value || '').trim()
  } catch {
    return
  }
  busyId.value = rid
  try {
    await api.post(`/admin/invoices/${rid}/reject`, { reason })
    ElMessage.success('已驳回')
    await load()
  } catch (e: any) {
    ElMessage.error(e.message || '操作失败')
  } finally {
    busyId.value = null
  }
}

onMounted(async () => {
  const qStatus = String(route.query.status || '').toUpperCase()
  if (qStatus && STATUS_LABELS[qStatus]) statusFilter.value = qStatus
  await loadStores()
  await load()
})
</script>

<style scoped>
.toolbar {
  margin-bottom: 12px;
}

.muted {
  color: #c0c4cc;
}

.create-hint {
  margin: 0 0 16px;
  font-size: 13px;
  color: #909399;
  line-height: 1.5;
}

.switch-hint {
  margin-left: 10px;
  font-size: 12px;
  color: #909399;
}

.order-toolbar {
  display: flex;
  gap: 8px;
  width: 100%;
  margin-bottom: 8px;
}

.order-pick-table {
  width: 100%;
  margin-top: 4px;
}

.order-pick-table :deep(.el-table__row) {
  cursor: pointer;
}

.order-pick-table :deep(.is-picked > td) {
  background: #ecf5ff !important;
}

.order-amt {
  color: #f56c6c;
  font-weight: 600;
}

.order-empty {
  margin: 6px 0 0;
  font-size: 13px;
  color: #909399;
}
</style>
