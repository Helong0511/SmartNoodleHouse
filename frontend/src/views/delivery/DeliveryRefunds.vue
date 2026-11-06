<template>
  <div class="refunds-page">
    <div class="toolbar">
      <el-select v-if="auth.isSuperAdmin" v-model="storeFilter" placeholder="全部门店" clearable style="width: 160px"
        @change="reloadRefunds">
        <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
      </el-select>
      <el-tag v-else-if="boundStoreName" type="info">门店：{{ boundStoreName }}</el-tag>
      <el-select v-model="refundFilter" placeholder="状态" clearable style="width: 120px" @change="reloadRefunds">
        <el-option label="待审核" value="PENDING" />
        <el-option label="平台处理中（需核实）" value="PROCESSING" />
        <el-option label="待补记账" value="APPROVED_LEDGER_PENDING" />
        <el-option label="已同意" value="APPROVED" />
        <el-option label="已拒绝" value="REJECTED" />
      </el-select>
      <el-select v-model="platformFilter" placeholder="平台" clearable style="width: 110px" @change="reloadRefunds">
        <el-option label="美团" value="MEITUAN" />
        <el-option label="饿了么" value="ELEME" />
      </el-select>
      <el-input v-model="keyword" clearable placeholder="平台单号" style="width: 180px" @keyup.enter="reloadRefunds"
        @clear="reloadRefunds" />
      <el-date-picker v-model="dateRange" type="daterange" value-format="YYYY-MM-DD" start-placeholder="开始"
        end-placeholder="结束" style="width: 240px" @change="reloadRefunds" />
      <el-button @click="reloadRefunds">查询</el-button>
    </div>
    <div v-if="hasNewHint" class="new-hint" @click="jumpToLatest">
      有退款状态更新，点击回到第 1 页查看最新
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
      <el-table-column label="类型" width="80">
        <template #default="{ row }">{{ label(DELIVERY_REFUND_TYPE, row.refund_type) }}</template>
      </el-table-column>
      <el-table-column prop="reason" label="原因" min-width="180" show-overflow-tooltip />
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag size="small" :type="row.status === 'PENDING' ? 'danger' : row.status === 'PROCESSING' || row.status === 'APPROVED_LEDGER_PENDING' ? 'warning' : 'info'">
            {{ label(REFUND_APPLY_STATUS, row.status) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="申请时间" width="150">
        <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="250" fixed="right">
        <template #default="{ row }">
          <template v-if="row.status === 'PENDING' && canHandleRefund">
            <el-button size="small" type="success" @click="handleRefund(row.id, 'approve')">同意</el-button>
            <el-button size="small" type="danger" @click="askRejectRefund(row)">拒绝</el-button>
          </template>
          <el-button
            v-else-if="row.status === 'APPROVED_LEDGER_PENDING' && canHandleRefund"
            size="small"
            type="warning"
            @click="handleRefund(row.id, 'approve', undefined, true)"
          >补记账</el-button>
          <div v-else-if="row.status === 'PROCESSING' && auth.isSuperAdmin" class="reconcile-actions">
            <el-button size="small" type="success" @click="reconcileRefund(row, 'platform_succeeded')">核实已退款</el-button>
            <el-button size="small" type="warning" @click="reconcileRefund(row, 'platform_not_applied')">核实未执行</el-button>
          </div>
          <span v-else-if="row.status === 'PROCESSING'" class="muted">请超级管理员核实平台结果，禁止重复退款</span>
          <span v-else-if="row.status === 'APPROVED_LEDGER_PENDING'" class="muted">仅有退款权限的人员可补记账</span>
          <span v-else-if="row.status === 'PENDING'" class="muted">仅店长可处理</span>
          <span v-else class="muted">已处理</span>
        </template>
      </el-table-column>
    </el-table>
    <div class="pager">
      <el-pagination v-model:current-page="refundPager.page" v-model:page-size="refundPager.pageSize"
        :total="refundPager.total" :page-sizes="[20, 50, 100]" layout="total, sizes, prev, pager, next" background small
        @current-change="loadRefunds" @size-change="reloadRefunds" />
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
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api'
import { useStoreOptions } from '@/composables/useStoreOptions'
import { useAuthStore } from '@/stores/auth'
import { DELIVERY_REFUND_TYPE, REFUND_APPLY_STATUS, label } from '@/utils/labels'

const auth = useAuthStore()
const canHandleRefund = computed(() => auth.hasPermission('payment:refund'))
const { storeOptions: stores, loadStores } = useStoreOptions()
const storeFilter = ref<number | undefined>()
const boundStoreName = computed(() => {
  const sid = auth.user?.store_id
  if (!sid) return ''
  return stores.value.find((s) => s.id === sid)?.name || `门店#${sid}`
})

const refunds = ref<any[]>([])
const refundFilter = ref('PENDING')
const platformFilter = ref('')
const keyword = ref('')
const dateRange = ref<string[] | null>(null)
const refundPager = reactive({ page: 1, pageSize: 20, total: 0 })
// 非第 1 页轮询不替换列表，仅提示，避免审批时翻页跳动
const hasNewHint = ref(false)

const rejectVisible = ref(false)
const rejectReason = ref('餐品已制作，无法退款')
const rejectId = ref<number | null>(null)

let pollTimer: ReturnType<typeof setInterval> | null = null

function platformName(p: string) {
  return p === 'MEITUAN' ? '美团' : p === 'ELEME' ? '饿了么' : p || '-'
}

function fmtTime(v?: string | null) {
  if (!v) return '-'
  return String(v).replace('T', ' ').slice(0, 19)
}

async function loadRefunds() {
  const params: Record<string, any> = {
    page: refundPager.page,
    page_size: refundPager.pageSize,
    store_id: storeFilter.value,
  }
  if (refundFilter.value) params.status = refundFilter.value
  if (platformFilter.value) params.platform = platformFilter.value
  if (keyword.value.trim()) params.keyword = keyword.value.trim()
  if (dateRange.value?.length === 2) {
    params.date_from = dateRange.value[0]
    params.date_to = dateRange.value[1]
  }
  let res
  try {
    res = await api.get('/delivery/refunds', { params })
  } catch (e: any) {
    // 10 秒轮询的网络抖动不应持续抛未处理 rejection
    return
  }
  const items = res.data?.items || []
  // 审批后当前筛选结果集缩小，末页变空时自动回退，避免停在空白页
  if (items.length === 0 && refundPager.page > 1) {
    refundPager.page -= 1
    return loadRefunds()
  }
  refunds.value = items
  refundPager.total = res.data?.total || 0
}

function reloadRefunds() {
  hasNewHint.value = false
  refundPager.page = 1
  return loadRefunds()
}

/** 轮询自动刷新：仅第 1 页替换列表，非第 1 页只亮提示条 */
async function autoReloadRefunds() {
  if (refundPager.page !== 1) {
    hasNewHint.value = true
    return
  }
  await loadRefunds()
}

async function jumpToLatest() {
  hasNewHint.value = false
  refundPager.page = 1
  await loadRefunds()
}

async function handleRefund(
  id: number,
  action: 'approve' | 'reject',
  reason?: string,
  ledgerRecovery = false,
) {
  try {
    await api.post(`/delivery/refunds/${id}/action`, {
      action,
      reason: action === 'approve' ? undefined : reason || '商家拒绝',
    })
    ElMessage.success(ledgerRecovery ? '财务分录补记成功' : action === 'approve' ? '已同意退款' : '已拒绝退款')
    await loadRefunds()
  } catch (e: any) {
    ElMessage.error(e.message || '操作失败')
  }
}

async function reconcileRefund(
  row: any,
  action: 'platform_succeeded' | 'platform_not_applied',
) {
  const succeeded = action === 'platform_succeeded'
  try {
    const { value } = await ElMessageBox.prompt(
      '请填写平台后台核实依据，至少 8 个字；建议包含平台退款单号、平台状态和核实时间。',
      succeeded ? '核实平台已退款成功' : '核实平台尚未执行退款',
      {
        inputType: 'textarea',
        inputPlaceholder: '例如：平台退款单 RFxxxx 状态为 SUCCESS，核实时间 2026-11-06 10:30',
        inputValidator: (value: string) => value.trim().length >= 8,
        inputErrorMessage: '核实依据至少填写 8 个字',
        confirmButtonText: '确认核实并恢复',
        cancelButtonText: '取消',
      },
    )
    const prefix = succeeded ? '平台核实已成功：' : '平台核实未执行：'
    const result = await api.post('/delivery/refunds/' + row.id + '/reconcile', {
      action,
      reason: prefix + value.trim(),
    })
    ElMessage.success(
      succeeded
        ? (result.data?.status === 'APPROVED' ? '退款账务已完成核对' : '已记录平台成功，财务分录待补记')
        : '已恢复待审核状态，可在确认安全后重新处理',
    )
    await loadRefunds()
  } catch (e: any) {
    // 即使补记账失败，后端也已持久化 APPROVED_LEDGER_PENDING；刷新显示真实状态。
    await loadRefunds()
    if (e !== 'cancel' && e !== 'close') {
      ElMessage.error(e.message || '退款结果核实失败')
    }
  }
}

function askRejectRefund(row: any) {
  rejectId.value = row.id
  rejectReason.value = '餐品已制作，无法退款'
  rejectVisible.value = true
}

async function doRejectRefund() {
  if (!rejectId.value) return
  if (!rejectReason.value?.trim()) {
    ElMessage.warning('请填写拒绝原因')
    return
  }
  await handleRefund(rejectId.value, 'reject', rejectReason.value.trim())
  rejectVisible.value = false
}

function startPoll() {
  if (pollTimer) return
  pollTimer = setInterval(() => autoReloadRefunds(), 10000)
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

.reconcile-actions {
  display: flex;
  gap: 4px;
  align-items: center;
}
</style>
