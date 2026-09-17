<template>
  <div class="refunds-page">
    <div class="toolbar">
      <el-select
        v-if="auth.isSuperAdmin"
        v-model="storeFilter"
        placeholder="全部门店"
        clearable
        style="width: 160px"
        @change="reloadRefunds"
      >
        <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
      </el-select>
      <el-tag v-else-if="boundStoreName" type="info">门店：{{ boundStoreName }}</el-tag>
      <el-select v-model="refundFilter" placeholder="状态" clearable style="width: 120px" @change="reloadRefunds">
        <el-option label="待审核" value="PENDING" />
        <el-option label="已同意" value="APPROVED" />
        <el-option label="已拒绝" value="REJECTED" />
      </el-select>
      <el-button @click="reloadRefunds">刷新</el-button>
    </div>
    <el-table :data="refunds" size="small" stripe>
      <el-table-column label="平台" width="72">
        <template #default="{ row }">{{ platformName(row.platform) }}</template>
      </el-table-column>
      <el-table-column prop="platform_day_seq" label="#" width="56" />
      <el-table-column prop="platform_order_id" label="平台单号" min-width="140" show-overflow-tooltip />
      <el-table-column prop="amount" label="金额" width="88">
        <template #default="{ row }"><span class="money">¥{{ row.amount }}</span></template>
      </el-table-column>
      <el-table-column prop="refund_type" label="类型" width="80" />
      <el-table-column prop="reason" label="原因" min-width="180" show-overflow-tooltip />
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag size="small" :type="row.status === 'PENDING' ? 'danger' : 'info'">
            {{ label(REFUND_APPLY_STATUS, row.status) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="申请时间" width="150">
        <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="180" fixed="right">
        <template #default="{ row }">
          <template v-if="row.status === 'PENDING' && canHandleRefund">
            <el-button size="small" type="success" @click="handleRefund(row.id, true)">同意</el-button>
            <el-button size="small" type="danger" @click="askRejectRefund(row)">拒绝</el-button>
          </template>
          <span v-else-if="row.status === 'PENDING'" class="muted">仅店长可处理</span>
          <span v-else class="muted">已处理</span>
        </template>
      </el-table-column>
    </el-table>
    <div class="pager">
      <el-pagination
        v-model:current-page="refundPager.page"
        v-model:page-size="refundPager.pageSize"
        :total="refundPager.total"
        layout="total, prev, pager, next"
        background
        small
        @current-change="loadRefunds"
        @size-change="reloadRefunds"
      />
    </div>

    <!-- 拒绝退款 -->
    <el-dialog v-model="rejectVisible" title="拒绝退款" width="420px">
      <el-input v-model="rejectReason" type="textarea" :rows="3" placeholder="请填写拒绝原因" />
      <template #footer>
        <el-button @click="rejectVisible = false">关闭</el-button>
        <el-button type="danger" @click="doRejectRefund">确认拒绝</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api'
import { useAuthStore } from '@/stores/auth'
import { REFUND_APPLY_STATUS, label } from '@/utils/labels'

const auth = useAuthStore()
const canHandleRefund = computed(() => auth.hasPermission('payment:refund'))
const stores = ref<any[]>([])
const storeFilter = ref<number | undefined>()
const boundStoreName = computed(() => {
  const sid = auth.user?.store_id
  if (!sid) return ''
  return stores.value.find((s) => s.id === sid)?.name || `门店#${sid}`
})

const refunds = ref<any[]>([])
const refundFilter = ref('PENDING')
const refundPager = reactive({ page: 1, pageSize: 20, total: 0 })

const rejectVisible = ref(false)
const rejectReason = ref('商品已制作，无法退款')
const rejectId = ref<number | null>(null)

let pollTimer: ReturnType<typeof setInterval> | null = null

function platformName(p: string) {
  return p === 'MEITUAN' ? '美团' : p === 'ELEME' ? '饿了么' : p || '-'
}

function fmtTime(v?: string | null) {
  if (!v) return '-'
  return String(v).replace('T', ' ').slice(0, 19)
}

async function loadStores() {
  try {
    const res = await api.get('/settings/stores/options')
    stores.value = res.data.items || []
  } catch {
    stores.value = []
  }
}

async function loadRefunds() {
  const params: Record<string, any> = {
    page: refundPager.page,
    page_size: refundPager.pageSize,
    store_id: storeFilter.value,
  }
  if (refundFilter.value) params.status = refundFilter.value
  const res = await api.get('/delivery/refunds', { params })
  refunds.value = res.data?.items || []
  refundPager.total = res.data?.total || 0
}

function reloadRefunds() {
  refundPager.page = 1
  return loadRefunds()
}

async function handleRefund(id: number, approve: boolean, reason?: string) {
  try {
    await api.post(`/delivery/refunds/${id}/action`, {
      approve,
      reason: approve ? undefined : reason || '商家拒绝',
    })
    ElMessage.success(approve ? '已同意退款' : '已拒绝退款')
    await loadRefunds()
  } catch (e: any) {
    ElMessage.error(e.message || '操作失败')
  }
}

function askRejectRefund(row: any) {
  rejectId.value = row.id
  rejectReason.value = '商品已制作，无法退款'
  rejectVisible.value = true
}

async function doRejectRefund() {
  if (!rejectId.value) return
  if (!rejectReason.value?.trim()) {
    ElMessage.warning('请填写拒绝原因')
    return
  }
  await handleRefund(rejectId.value, false, rejectReason.value.trim())
  rejectVisible.value = false
}

function startPoll() {
  if (pollTimer) return
  pollTimer = setInterval(() => loadRefunds(), 10000)
}

function stopPoll() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

onMounted(async () => {
  await loadStores()
  await reloadRefunds()
  startPoll()
})

onUnmounted(() => {
  stopPoll()
})
</script>

<style scoped>
.refunds-page {
  min-height: 400px;
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
.muted {
  color: #c0c4cc;
}
</style>
