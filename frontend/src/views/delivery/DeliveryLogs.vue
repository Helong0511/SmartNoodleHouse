<template>
  <div class="logs-page">
    <div class="toolbar">
      <el-select v-model="logPlatform" clearable placeholder="平台" style="width: 110px" @change="loadLogs">
        <el-option label="美团" value="MEITUAN" />
        <el-option label="饿了么" value="ELEME" />
      </el-select>
      <el-button @click="loadLogs">刷新</el-button>
    </div>
    <el-table :data="logs" size="small" stripe>
      <el-table-column prop="platform" label="平台" width="80" />
      <el-table-column prop="event_type" label="事件" width="140" show-overflow-tooltip />
      <el-table-column prop="platform_order_id" label="平台单号" min-width="140" show-overflow-tooltip />
      <el-table-column prop="order_id" label="订单ID" width="90" />
      <el-table-column label="时间" width="160">
        <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
      </el-table-column>
    </el-table>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import api from '@/api'

const logPlatform = ref('')
const logs = ref<any[]>([])

function fmtTime(v?: string | null) {
  if (!v) return '-'
  return String(v).replace('T', ' ').slice(0, 19)
}

async function loadLogs() {
  const params: Record<string, any> = { page_size: 50 }
  if (logPlatform.value) params.platform = logPlatform.value
  const res = await api.get('/delivery/callback-logs', { params })
  logs.value = res.data?.items || []
}

onMounted(() => {
  loadLogs()
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
.muted {
  color: #c0c4cc;
}
</style>
