<template>
  <div class="sync-page">
    <div class="toolbar">
      <el-button type="primary" :loading="reconciling" @click="reconcileNow">立即库存对账</el-button>
      <el-button @click="loadOverview(); loadAlerts()">刷新</el-button>
      <el-tag v-if="unresolvedAlerts" type="danger" effect="light">未处理告警 {{ unresolvedAlerts }}</el-tag>
      <span class="muted" style="font-size: 12px">系统每 5 分钟自动对账一次；库存售罄/补货为实时镜像。</span>
    </div>

    <h4 style="margin: 4px 0 8px">平台门店状态</h4>
    <el-table :data="overview" size="small" stripe>
      <el-table-column prop="platform" label="平台" width="90">
        <template #default="{ row }">{{ platformName(row.platform) }}</template>
      </el-table-column>
      <el-table-column prop="platform_shop_name" label="平台门店" min-width="120" show-overflow-tooltip />
      <el-table-column label="绑定" width="70">
        <template #default="{ row }">
          <el-tag size="small" :type="row.status === 1 ? 'success' : 'info'">
            {{ row.status === 1 ? '启用' : '停用' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="平台营业" width="90">
        <template #default="{ row }">
          <el-tag size="small" :type="openStatusMeta(row.platform_open_status).type">
            {{ openStatusMeta(row.platform_open_status).text }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="自动开关" width="80" align="center">
        <template #default="{ row }">{{ row.auto_sync_open ? '开' : '关' }}</template>
      </el-table-column>
      <el-table-column label="Token" width="150">
        <template #default="{ row }">
          <span v-if="!row.token_expires_at" class="muted">—</span>
          <el-tag v-else size="small" :type="tokenExpiryTagType(row.token_expires_at)">
            {{ fmtExpires(row.token_expires_at) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="最近同步" min-width="150">
        <template #default="{ row }">{{ row.last_sync_at ? fmtTime(row.last_sync_at) : '—' }}</template>
      </el-table-column>
    </el-table>

    <div class="alerts-head">
      <h4>同步 / 拒单告警</h4>
      <el-select v-model="alertFilter.platform" placeholder="平台" clearable size="small" style="width:100px" @change="reloadAlerts">
        <el-option label="美团" value="MEITUAN" />
        <el-option label="饿了么" value="ELEME" />
      </el-select>
      <el-select v-model="alertFilter.alertType" placeholder="类型" clearable size="small" style="width:110px" @change="reloadAlerts">
        <el-option label="库存拒单" value="REJECT_ORDER" />
        <el-option label="同步失败" value="SYNC_FAIL" />
      </el-select>
      <el-select v-model="alertFilter.resolved" placeholder="状态" clearable size="small" style="width:100px" @change="reloadAlerts">
        <el-option label="待处理" :value="0" />
        <el-option label="已处理" :value="1" />
      </el-select>
    </div>
    <el-table :data="alerts" size="small" stripe>
      <el-table-column label="类型" width="100">
        <template #default="{ row }">
          <el-tag size="small" :type="row.alert_type === 'REJECT_ORDER' ? 'danger' : 'warning'">
            {{ row.alert_type === 'REJECT_ORDER' ? '库存拒单' : '同步失败' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="platform" label="平台" width="90">
        <template #default="{ row }">{{ row.platform ? platformName(row.platform) : '—' }}</template>
      </el-table-column>
      <el-table-column prop="title" label="标题" min-width="180" show-overflow-tooltip />
      <el-table-column prop="detail" label="详情" min-width="160" show-overflow-tooltip />
      <el-table-column prop="platform_order_id" label="平台单号" min-width="130" show-overflow-tooltip />
      <el-table-column label="时间" width="160">
        <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="状态" width="80">
        <template #default="{ row }">
          <el-tag size="small" :type="row.resolved ? 'success' : 'danger'">
            {{ row.resolved ? '已处理' : '待处理' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="90" fixed="right">
        <template #default="{ row }">
          <el-button v-if="!row.resolved" size="small" type="primary" link @click="resolveAlert(row)">
            标记处理
          </el-button>
        </template>
      </el-table-column>
    </el-table>
    <div class="pager">
      <el-pagination
        v-model:current-page="alertPager.page"
        v-model:page-size="alertPager.pageSize"
        :total="alertPager.total"
        :page-sizes="[20, 50, 100]"
        layout="total, sizes, prev, pager, next"
        background
        small
        @current-change="loadAlerts"
        @size-change="reloadAlerts"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api'

const overview = ref<any[]>([])
const unresolvedAlerts = ref(0)
const alerts = ref<any[]>([])
const reconciling = ref(false)
const alertPager = reactive({ page: 1, pageSize: 20, total: 0 })
const alertFilter = reactive<{ platform: string; alertType: string; resolved: number | null }>({
  platform: '',
  alertType: '',
  resolved: null,
})

function platformName(p: string) {
  return p === 'MEITUAN' ? '美团' : p === 'ELEME' ? '饿了么' : p || '-'
}

function fmtTime(v?: string | null) {
  if (!v) return '-'
  return String(v).replace('T', ' ').slice(0, 19)
}

function fmtExpires(v?: string | null) {
  if (!v) return '—'
  const d = new Date(v)
  if (Number.isNaN(d.getTime())) return v
  const now = Date.now()
  const diff = d.getTime() - now
  const days = Math.floor(diff / 86400000)
  const hours = Math.floor((diff % 86400000) / 3600000)
  if (diff < 0) return '已过期'
  if (days > 0) return `${days}天${hours}时`
  return `${hours}时`
}

function tokenExpiryTagType(v?: string | null) {
  if (!v) return 'info' as const
  const diff = new Date(v).getTime() - Date.now()
  if (diff < 0) return 'danger' as const
  if (diff < 24 * 3600 * 1000) return 'warning' as const
  return 'success' as const
}

function openStatusMeta(s: string | null | undefined) {
  if (s === 'OPEN') return { text: '营业中', type: 'success' }
  if (s === 'REST') return { text: '休息中', type: 'info' }
  if (s === 'OFFLINE') return { text: '已下线', type: 'danger' }
  return { text: '未同步', type: 'info' }
}

async function loadOverview() {
  try {
    const res = await api.get('/delivery/sync/overview')
    overview.value = res.data?.bindings || []
    unresolvedAlerts.value = res.data?.unresolved_alerts || 0
  } catch (e: any) {
    ElMessage.error(e.message || '监控加载失败')
  }
}

async function loadAlerts() {
  try {
    const params: Record<string, any> = {
      page: alertPager.page,
      page_size: alertPager.pageSize,
    }
    if (alertFilter.platform) params.platform = alertFilter.platform
    if (alertFilter.alertType) params.alert_type = alertFilter.alertType
    if (alertFilter.resolved !== null) params.resolved = alertFilter.resolved
    const res = await api.get('/delivery/sync-alerts', { params })
    const items = res.data?.items || []
    // 标记处理后末页变空时自动回退
    if (items.length === 0 && alertPager.page > 1) {
      alertPager.page -= 1
      await loadAlerts()
      return
    }
    alerts.value = items
    alertPager.total = res.data?.total || 0
  } catch (e: any) {
    ElMessage.error(e.message || '告警加载失败')
  }
}

function reloadAlerts() {
  alertPager.page = 1
  return loadAlerts()
}

async function reconcileNow() {
  reconciling.value = true
  try {
    const res = await api.post('/delivery/sync/reconcile', {})
    ElMessage.success(`对账完成：${res.data?.products ?? 0} 项，失败 ${res.data?.failures ?? 0}`)
    await loadOverview()
  } catch (e: any) {
    ElMessage.error(e.message || '对账失败')
  } finally {
    reconciling.value = false
  }
}

async function resolveAlert(row: any) {
  try {
    await api.post(`/delivery/sync-alerts/${row.id}/resolve`)
    ElMessage.success('已标记处理')
    await Promise.all([loadAlerts(), loadOverview()])
  } catch (e: any) {
    ElMessage.error(e.message || '操作失败')
  }
}

onMounted(() => {
  loadOverview()
  loadAlerts()
})
</script>

<style scoped>
.sync-page {
  min-height: 400px;
}
.toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin-bottom: 12px;
}
.muted {
  color: #c0c4cc;
}
.alerts-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 14px 0 8px;
}
.alerts-head h4 {
  margin: 0 8px 0 0;
}
.pager {
  margin-top: 12px;
  display: flex;
  justify-content: flex-end;
}
</style>
