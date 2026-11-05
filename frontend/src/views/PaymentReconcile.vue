<template>
  <div v-loading="loading">
    <el-alert
      type="warning"
      :closable="false"
      show-icon
      class="tip"
      title="当前仅核对支付交易（trade_no / amount，可选 payment_no），不核对退款流水、手续费或结算净额。请先从官方账单筛选支付成功交易并裁剪成精简 CSV；退款必须单独对账，不能把退款行混入本批次。"
    />

    <el-card v-if="canManage" shadow="never" class="import-card">
      <el-form inline>
        <el-form-item label="渠道">
          <el-select v-model="importForm.channel" style="width:140px">
            <el-option label="微信" value="WECHAT" />
            <el-option label="支付宝" value="ALIPAY" />
          </el-select>
        </el-form-item>
        <el-form-item label="账单日">
          <el-date-picker
            v-model="importForm.bill_date"
            type="date"
            value-format="YYYY-MM-DD"
            placeholder="选择日期"
            style="width:160px"
          />
        </el-form-item>
        <el-form-item>
          <el-upload
            :show-file-list="false"
            accept=".csv,text/csv"
            :http-request="doImport"
            :disabled="importing"
          >
            <el-button type="primary" :loading="importing">导入账单并对账</el-button>
          </el-upload>
        </el-form-item>
        <el-button @click="reloadBatches">刷新批次</el-button>
      </el-form>
    </el-card>

    <div v-else class="toolbar">
      <el-button type="primary" @click="reloadBatches">刷新批次</el-button>
    </div>

    <el-table :data="batches" border highlight-current-row @current-change="onSelectBatch">
      <el-table-column prop="id" label="批次" width="80" />
      <el-table-column label="渠道" width="100">
        <template #default="{ row }">{{ channelLabel(row.channel) }}</template>
      </el-table-column>
      <el-table-column prop="bill_date" label="账单日" width="120" />
      <el-table-column prop="status" label="状态" width="100" />
      <el-table-column prop="local_count" label="本地笔数" width="100" />
      <el-table-column prop="channel_count" label="渠道笔数" width="100" />
      <el-table-column prop="mismatch_count" label="差异笔数" width="100" />
      <el-table-column label="创建时间" min-width="170">
        <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="100" fixed="right">
        <template #default="{ row }">
          <el-button type="primary" link @click="loadDiffs(row)">差异</el-button>
        </template>
      </el-table-column>
    </el-table>

    <div class="pager">
      <el-pagination
        v-model:current-page="pager.page"
        v-model:page-size="pager.pageSize"
        :total="pager.total"
        :page-sizes="[20, 50, 100]"
        layout="total, sizes, prev, pager, next"
        background
        @current-change="loadBatches"
        @size-change="reloadBatches"
      />
    </div>

    <el-drawer v-model="diffVisible" :title="diffTitle" size="640px">
      <el-table :data="diffs" border v-loading="diffLoading" max-height="70vh">
        <el-table-column label="类型" width="120">
          <template #default="{ row }">{{ diffTypeLabel(row.diff_type) }}</template>
        </el-table-column>
        <el-table-column prop="local_payment_no" label="本地支付号" min-width="140" show-overflow-tooltip />
        <el-table-column prop="channel_trade_no" label="渠道交易号" min-width="140" show-overflow-tooltip />
        <el-table-column prop="local_amount" label="本地金额" width="100" />
        <el-table-column prop="channel_amount" label="渠道金额" width="100" />
        <el-table-column prop="status" label="状态" width="90" />
      </el-table>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api'
import { useAuthStore } from '@/stores/auth'
import { CHANNEL } from '@/utils/labels'

const auth = useAuthStore()
const canManage = computed(() => auth.hasPermission('reconcile:manage'))

const loading = ref(false)
const importing = ref(false)
const batches = ref<any[]>([])
const diffs = ref<any[]>([])
const diffLoading = ref(false)
const diffVisible = ref(false)
const selectedBatch = ref<any>(null)
const pager = reactive({ page: 1, pageSize: 20, total: 0 })

const importForm = reactive({
  channel: 'WECHAT',
  bill_date: '',
})

const DIFF_TYPE: Record<string, string> = {
  MATCH: '匹配',
  AMOUNT_MISMATCH: '金额不符',
  LOCAL_ONLY: '仅本地有',
  CHANNEL_ONLY: '仅渠道有',
  MISSING_LOCAL: '缺本地',
  MISSING_CHANNEL: '缺渠道',
}

const diffTitle = computed(() =>
  selectedBatch.value
    ? `批次 #${selectedBatch.value.id} 差异（${selectedBatch.value.mismatch_count ?? diffs.value.length}）`
    : '对账差异',
)

function channelLabel(code: string) {
  return CHANNEL[code] || code || '—'
}

function diffTypeLabel(code: string) {
  return DIFF_TYPE[code] || code || '—'
}

function formatDateTime(iso?: string | null) {
  if (!iso) return '—'
  return String(iso).replace('T', ' ').slice(0, 19)
}

async function loadBatches() {
  loading.value = true
  try {
    const res = await api.get('/reconcile/payments/batches', {
      params: { page: pager.page, page_size: pager.pageSize },
    })
    batches.value = res.data?.items || []
    pager.total = Number(res.data?.total || 0)
    const maxPage = Math.max(1, Number(res.data?.total_pages || 1))
    if (pager.page > maxPage) {
      pager.page = maxPage
      if (batches.value.length === 0 && pager.total > 0) await loadBatches()
    }
  } catch (e: any) {
    ElMessage.error(e.message || '加载对账批次失败')
  } finally {
    loading.value = false
  }
}

function reloadBatches() {
  pager.page = 1
  return loadBatches()
}

async function loadDiffs(row: any) {
  selectedBatch.value = row
  diffVisible.value = true
  diffLoading.value = true
  try {
    const res = await api.get(`/reconcile/payments/batches/${row.id}/diffs`)
    diffs.value = res.data?.items || []
  } catch (e: any) {
    ElMessage.error(e.message || '加载差异失败')
    diffs.value = []
  } finally {
    diffLoading.value = false
  }
}

function onSelectBatch(row: any) {
  if (row) loadDiffs(row)
}

async function doImport(option: { file: File }) {
  if (!importForm.bill_date) {
    ElMessage.warning('请先选择账单日')
    return
  }
  importing.value = true
  try {
    const fd = new FormData()
    fd.append('channel', importForm.channel)
    fd.append('bill_date', importForm.bill_date)
    fd.append('file', option.file)
    const res = await api.post('/reconcile/payments/import', fd)
    const b = res.data || {}
    ElMessage.success(
      `对账完成：本地 ${b.local_count ?? 0} / 渠道 ${b.channel_count ?? 0}，差异 ${b.mismatch_count ?? 0}`,
    )
    await reloadBatches()
    if (b.id) {
      const row = batches.value.find((x) => x.id === b.id) || b
      await loadDiffs(row)
    }
  } catch (e: any) {
    ElMessage.error(e.message || '导入失败')
  } finally {
    importing.value = false
  }
}

onMounted(loadBatches)
</script>

<style scoped>
.tip {
  margin-bottom: 12px;
}
.import-card {
  margin-bottom: 12px;
}
.toolbar {
  margin-bottom: 12px;
}
.pager {
  margin-top: 12px;
  display: flex;
  justify-content: flex-end;
}
</style>
