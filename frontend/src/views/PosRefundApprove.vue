<template>
  <div class="refund-page">
    <div class="toolbar">
      <el-select v-model="status" placeholder="状态" clearable size="large" style="width:140px" @change="load">
        <el-option label="待审批" value="PENDING" />
        <el-option label="已退款" value="SUCCESS" />
        <el-option label="已拒绝" value="REJECTED" />
        <el-option label="失败" value="FAILED" />
        <el-option label="已撤销" value="CANCELLED" />
      </el-select>
      <el-select
        v-if="auth.isSuperAdmin"
        v-model="storeId"
        placeholder="门店"
        size="large"
        style="width:200px"
        @change="load"
      >
        <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
      </el-select>
      <el-button type="primary" size="large" :loading="loading" @click="load">刷新</el-button>
    </div>

    <el-table :data="items" v-loading="loading" stripe style="width:100%;margin-top:12px">
      <el-table-column prop="order_no" label="订单号" min-width="160" show-overflow-tooltip />
      <el-table-column prop="requester_name" label="申请人" width="100" />
      <el-table-column label="金额" width="100">
        <template #default="{ row }"><b class="amt">¥{{ row.amount }}</b></template>
      </el-table-column>
      <el-table-column label="支付方式" width="110">
        <template #default="{ row }">
          <div>{{ payLabel(row.payment_method) }}</div>
          <div v-if="row.tenders?.length > 1" class="tender-tip">
            {{ row.tenders.map((t: any) => `${payLabel(t.provider)}¥${t.refundable}`).join(' · ') }}
          </div>
        </template>
      </el-table-column>
      <el-table-column prop="reason" label="原因" min-width="140" show-overflow-tooltip />
      <el-table-column label="桌台/外带" width="100">
        <template #default="{ row }">
          {{ row.dining_type === 'TAKEAWAY' ? '外带' : (row.table_name || row.table_no || '—') }}
        </template>
      </el-table-column>
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag :type="tagType(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="created_at" label="申请时间" min-width="160" show-overflow-tooltip />
      <el-table-column label="操作" width="220" fixed="right">
        <template #default="{ row }">
          <template v-if="row.status === 'PENDING'">
            <el-button type="success" size="small" :loading="actingId === row.id" @click="approve(row)">
              通过并退款
            </el-button>
            <el-button type="danger" plain size="small" :loading="actingId === row.id" @click="reject(row)">
              拒绝
            </el-button>
          </template>
          <el-button
            v-else-if="row.status === 'FAILED'"
            type="warning"
            size="small"
            :loading="actingId === row.id"
            @click="approve(row)"
          >
            重试退款
          </el-button>
          <span v-else class="muted">{{ row.review_remark || row.fail_reason || '—' }}</span>
        </template>
      </el-table-column>
    </el-table>
    <div v-if="!loading && !items.length" class="empty">暂无退款申请</div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const loading = ref(false)
const items = ref<any[]>([])
const status = ref<string | null>('PENDING')
const storeId = ref<number | null>(null)
const stores = ref<any[]>([])
const actingId = ref<number | null>(null)

function payLabel(m: string) {
  const u = (m || '').toUpperCase()
  if (u === 'CASH') return '现金'
  if (u === 'WECHAT') return '微信'
  if (u === 'ALIPAY') return '支付宝'
  if (u === 'MIXED') return '组合支付'
  return m || '—'
}

function statusLabel(s: string) {
  const map: Record<string, string> = {
    PENDING: '待审批',
    SUCCESS: '已退款',
    REJECTED: '已拒绝',
    FAILED: '失败',
    CANCELLED: '已撤销',
  }
  return map[s] || s
}

function tagType(s: string) {
  if (s === 'PENDING') return 'warning'
  if (s === 'SUCCESS') return 'success'
  if (s === 'FAILED' || s === 'REJECTED') return 'danger'
  return 'info'
}

async function loadStores() {
  if (!auth.isSuperAdmin) {
    storeId.value = auth.user?.store_id ?? null
    return
  }
  try {
    const res = await api.get('/orders/kitchen/stores')
    stores.value = res.data?.items || res.data || []
    if (!storeId.value && stores.value.length) storeId.value = stores.value[0].id
  } catch {
    /* ignore */
  }
}

async function load() {
  loading.value = true
  try {
    const params: Record<string, any> = { page: 1, page_size: 100 }
    if (status.value) params.status = status.value
    if (storeId.value) params.store_id = storeId.value
    const res = await api.get('/pos/refund-requests', { params })
    items.value = res.data?.items || []
  } catch (e: any) {
    ElMessage.error(e.message || '加载失败')
  } finally {
    loading.value = false
  }
}

async function approve(row: any) {
  const tenderHint =
    row.tenders?.length > 1
      ? `\n分摊预览（现金优先）：${row.tenders.map((t: any) => `${payLabel(t.provider)}可退¥${t.refundable}`).join(' · ')}`
      : ''
  try {
    await ElMessageBox.confirm(
      `确认通过并对订单 ${row.order_no} 退款 ¥${row.amount}？\n微信/支付宝原路退回，现金当面退给顾客。${tenderHint}`,
      '审批并执行退款',
      { type: 'warning', confirmButtonText: '通过并退款' },
    )
  } catch {
    return
  }
  actingId.value = row.id
  try {
    await api.post(`/pos/refund-requests/${row.id}/approve`)
    ElMessage.success('已退款')
    await load()
  } catch (e: any) {
    ElMessage.error(e.message || '退款失败')
    await load()
  } finally {
    actingId.value = null
  }
}

async function reject(row: any) {
  let remark = ''
  try {
    const { value } = await ElMessageBox.prompt('请输入拒绝原因（可选）', '拒绝退款', {
      confirmButtonText: '拒绝',
      cancelButtonText: '取消',
      inputPlaceholder: '拒绝原因',
    })
    remark = value || ''
  } catch {
    return
  }
  actingId.value = row.id
  try {
    await api.post(`/pos/refund-requests/${row.id}/reject`, { remark: remark || null })
    ElMessage.success('已拒绝')
    await load()
  } catch (e: any) {
    ElMessage.error(e.message || '操作失败')
  } finally {
    actingId.value = null
  }
}

onMounted(async () => {
  if (!auth.user) await auth.fetchMe()
  await loadStores()
  await load()
})
</script>

<style scoped>
.refund-page { padding: 8px 4px 24px; }
.toolbar { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
.amt { color: #ff6b35; }
.tender-tip { font-size: 12px; color: #888; margin-top: 2px; line-height: 1.3; }
.empty { text-align: center; color: #aaa; padding: 40px; }
.muted { color: #999; font-size: 13px; }
</style>
