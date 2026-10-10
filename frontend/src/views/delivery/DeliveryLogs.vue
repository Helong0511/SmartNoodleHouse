<template>
  <div class="logs-page" v-loading="loading">
    <div class="toolbar">
      <el-select v-if="auth.isSuperAdmin" v-model="storeFilter" clearable placeholder="全部门店" style="width: 160px"
        @change="reload">
        <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
      </el-select>
      <el-tag v-else-if="boundStoreName" type="info" class="store-tag">门店：{{ boundStoreName }}</el-tag>
      <el-select v-model="logPlatform" clearable placeholder="平台" style="width: 110px" @change="onPlatformChange">
        <el-option label="美团" value="MEITUAN" />
        <el-option label="饿了么" value="ELEME" />
      </el-select>
      <el-select v-model="eventType" clearable filterable allow-create default-first-option placeholder="事件类型"
        style="width: 200px" @change="reload">
        <el-option v-for="t in eventTypeOptions" :key="t" :label="label(DELIVERY_LOG_EVENT, t)" :value="t" />
      </el-select>
      <el-date-picker v-model="dateRange" type="daterange" value-format="YYYY-MM-DD" start-placeholder="开始"
        end-placeholder="结束" style="width: 240px" @change="reload" />
      <el-button type="primary" @click="reload">查询</el-button>
      <el-button @click="resetFilters">重置</el-button>
    </div>
    <el-table :data="logs" size="small" stripe>
      <el-table-column label="平台" width="80">
        <template #default="{ row }">{{ platformName(row.platform) }}</template>
      </el-table-column>
      <el-table-column label="事件" width="180" show-overflow-tooltip>
        <template #default="{ row }">{{ label(DELIVERY_LOG_EVENT, row.event_type) }}</template>
      </el-table-column>
      <el-table-column prop="platform_order_id" label="平台单号" min-width="160" show-overflow-tooltip />
      <el-table-column prop="order_id" label="订单ID" width="90" />
      <el-table-column label="时间" width="170">
        <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
      </el-table-column>
    </el-table>
    <div class="pager">
      <el-pagination v-model:current-page="pager.page" v-model:page-size="pager.pageSize" :total="pager.total"
        :page-sizes="[20, 50, 100]" layout="total, sizes, prev, pager, next" background small @current-change="loadLogs"
        @size-change="reload" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api'
import { useStoreOptions } from '@/composables/useStoreOptions'
import { useAuthStore } from '@/stores/auth'
import { DELIVERY_LOG_EVENT, label } from '@/utils/labels'

const auth = useAuthStore()
const loading = ref(false)
const { storeOptions: stores, loadStores } = useStoreOptions()
const storeFilter = ref<number | undefined>()
const logPlatform = ref('')
const eventType = ref('')
const dateRange = ref<string[] | null>(null)
const logs = ref<any[]>([])
const eventTypeOptions = ref<string[]>([])
const pager = reactive({ page: 1, pageSize: 50, total: 0 })

const boundStoreName = computed(() => {
  const sid = auth.user?.store_id
  if (!sid) return ''
  return stores.value.find((s) => s.id === sid)?.name || `门店#${sid}`
})

function todayRange(): string[] {
  const d = new Date()
  const t = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  return [t, t]
}

function platformName(p: string) {
  return p === 'MEITUAN' ? '美团' : p === 'ELEME' ? '饿了么' : p || '-'
}

function fmtTime(v?: string | null) {
  if (!v) return '-'
  return String(v).replace('T', ' ').slice(0, 19)
}

async function loadLogs() {
  loading.value = true
  try {
    const params: Record<string, any> = {
      page: pager.page,
      page_size: pager.pageSize,
    }
    if (auth.isSuperAdmin && storeFilter.value) params.store_id = storeFilter.value
    if (logPlatform.value) params.platform = logPlatform.value
    if (eventType.value) params.event_type = eventType.value
    if (dateRange.value?.length === 2) {
      params.date_from = dateRange.value[0]
      params.date_to = dateRange.value[1]
    }
    const res = await api.get('/delivery/callback-logs', { params })
    const items = res.data?.items || []
    // 翻到越界空页时自动回退（如旧日志被清理）
    if (items.length === 0 && pager.page > 1) {
      pager.page -= 1
      await loadLogs()
      return
    }
    logs.value = items
    pager.total = Number(res.data?.total || 0)
    // 事件下拉跟随当前平台/过滤口径联动
    eventTypeOptions.value = res.data?.event_types || []
  } catch (e: any) {
    ElMessage.error(e.message || '加载失败')
  } finally {
    loading.value = false
  }
}

function reload() {
  pager.page = 1
  return loadLogs()
}

/** 平台切换后事件类型可选值集合不同，清空已选事件并重新查询 */
function onPlatformChange() {
  eventType.value = ''
  reload()
}

function resetFilters() {
  storeFilter.value = undefined
  logPlatform.value = ''
  eventType.value = ''
  dateRange.value = todayRange()
  reload()
}

onMounted(async () => {
  dateRange.value = todayRange()
  await loadStores()
  await loadLogs()
})
</script>

<style scoped>
.logs-page {
  min-height: 400px;
}

.toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin-bottom: 12px;
}

.store-tag {
  margin-right: 4px;
}

.pager {
  margin-top: 12px;
  display: flex;
  justify-content: flex-end;
}
</style>
