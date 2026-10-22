<template>
  <div v-loading="loading">
    <el-alert
      type="info"
      :closable="false"
      show-icon
      class="tip"
      title="冷归档将超过保留天数的历史订单写入归档快照；可先「试跑」查看将归档数量。"
    />

    <el-card v-if="canArchive" shadow="never" class="run-card">
      <el-form inline>
        <el-form-item label="保留天数">
          <el-input-number v-model="runForm.older_than_days" :min="30" :max="3650" :step="30" />
        </el-form-item>
        <el-form-item label="单次上限">
          <el-input-number v-model="runForm.limit" :min="1" :max="1000" :step="50" />
        </el-form-item>
        <el-button :loading="running" @click="runArchive(true)">试跑</el-button>
        <el-button type="danger" :loading="running" @click="confirmArchive">执行归档</el-button>
        <el-button @click="loadList">刷新列表</el-button>
      </el-form>
      <p v-if="lastResult" class="result-line">
        最近结果：候选 {{ lastResult.candidates ?? '—' }}，
        已归档 {{ lastResult.archived ?? '—' }}
        <span v-if="lastResult.dry_run">（试跑未写入）</span>
      </p>
    </el-card>

    <el-form inline class="toolbar">
      <el-form-item v-if="auth.isSuperAdmin">
        <el-select v-model="storeFilter" placeholder="全部门店" clearable style="width:160px" @change="reload">
          <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
        </el-select>
      </el-form-item>
      <el-tag v-else-if="boundStoreName" type="info" style="margin-right:12px">
        门店：{{ boundStoreName }}
      </el-tag>
      <el-button type="primary" @click="reload">查询</el-button>
    </el-form>

    <el-table :data="rows" border>
      <el-table-column prop="order_no" label="订单号" min-width="160" show-overflow-tooltip />
      <el-table-column label="门店" width="120">
        <template #default="{ row }">{{ storeName(row.store_id) }}</template>
      </el-table-column>
      <el-table-column prop="status" label="订单状态" width="110" />
      <el-table-column prop="payment_status" label="支付状态" width="110" />
      <el-table-column label="原下单时间" min-width="160">
        <template #default="{ row }">{{ formatDateTime(row.original_created_at) }}</template>
      </el-table-column>
      <el-table-column label="归档时间" min-width="160">
        <template #default="{ row }">{{ formatDateTime(row.archived_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="100" fixed="right">
        <template #default="{ row }">
          <el-button type="primary" link @click="openDetail(row)">详情</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-drawer v-model="detailVisible" :title="detailTitle" size="560px">
      <pre v-loading="detailLoading" class="payload">{{ detailText }}</pre>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api'
import { useAuthStore } from '@/stores/auth'
import { useStoreOptions } from '@/composables/useStoreOptions'

const auth = useAuthStore()
const canArchive = computed(() => auth.hasPermission('archive:manage'))

const loading = ref(false)
const running = ref(false)
const rows = ref<any[]>([])
const lastResult = ref<any>(null)
const storeFilter = ref<number | null>(null)
const boundStoreName = ref('')
const { storeOptions: stores, loadStores: fetchStores } = useStoreOptions()

const runForm = reactive({
  older_than_days: 365,
  limit: 200,
})

const detailVisible = ref(false)
const detailLoading = ref(false)
const detailPayload = ref<any>(null)
const detailOrderNo = ref('')
const detailTitle = computed(() => (detailOrderNo.value ? `归档详情 ${detailOrderNo.value}` : '归档详情'))
const detailText = computed(() => {
  if (!detailPayload.value) return '暂无数据'
  try {
    return JSON.stringify(detailPayload.value, null, 2)
  } catch {
    return String(detailPayload.value)
  }
})

function formatDateTime(iso?: string | null) {
  if (!iso) return '—'
  return String(iso).replace('T', ' ').slice(0, 19)
}

function storeName(sid: number) {
  if (!sid) return '—'
  return stores.value.find((s) => s.id === sid)?.name || `门店#${sid}`
}

async function loadStores() {
  if (!(await fetchStores())) return
  if (!auth.isSuperAdmin && auth.user?.store_id) {
    const s = stores.value.find((x) => x.id === auth.user?.store_id)
    boundStoreName.value = s?.name || `门店#${auth.user.store_id}`
  }
}

async function loadList() {
  loading.value = true
  try {
    const params: Record<string, any> = { limit: 50 }
    if (auth.isSuperAdmin && storeFilter.value) params.store_id = storeFilter.value
    const res = await api.get('/reconcile/orders/archived', { params })
    rows.value = res.data?.items || []
  } catch (e: any) {
    ElMessage.error(e.message || '加载归档列表失败')
  } finally {
    loading.value = false
  }
}

function reload() {
  return loadList()
}

async function runArchive(dryRun: boolean) {
  running.value = true
  try {
    const res = await api.post('/reconcile/orders/archive', {
      older_than_days: runForm.older_than_days,
      limit: runForm.limit,
      dry_run: dryRun,
    })
    lastResult.value = { ...(res.data || {}), dry_run: dryRun }
    ElMessage.success(dryRun ? '试跑完成' : '归档执行完成')
    if (!dryRun) await loadList()
  } catch (e: any) {
    ElMessage.error(e.message || '归档失败')
  } finally {
    running.value = false
  }
}

async function confirmArchive() {
  try {
    await ElMessageBox.confirm(
      `将归档超过 ${runForm.older_than_days} 天的订单（最多 ${runForm.limit} 条），确认执行？`,
      '执行冷归档',
      { type: 'warning', confirmButtonText: '执行', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  await runArchive(false)
}

async function openDetail(row: any) {
  detailOrderNo.value = row.order_no
  detailVisible.value = true
  detailLoading.value = true
  detailPayload.value = null
  try {
    const res = await api.get(`/reconcile/orders/archived/${encodeURIComponent(row.order_no)}`)
    detailPayload.value = res.data?.payload ?? res.data
  } catch (e: any) {
    ElMessage.error(e.message || '加载详情失败')
  } finally {
    detailLoading.value = false
  }
}

onMounted(async () => {
  await loadStores()
  await loadList()
})
</script>

<style scoped>
.tip {
  margin-bottom: 12px;
}
.run-card {
  margin-bottom: 12px;
}
.toolbar {
  margin-bottom: 12px;
}
.result-line {
  margin: 0;
  color: #606266;
  font-size: 13px;
}
.payload {
  margin: 0;
  padding: 12px;
  background: #f5f7fa;
  border-radius: 6px;
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 70vh;
  overflow: auto;
}
</style>
