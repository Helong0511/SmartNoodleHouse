<template>
  <div class="page">
    <div class="toolbar">
      <el-button type="primary" @click="load" :loading="loading">刷新报表</el-button>
      <el-button :disabled="!items.length" @click="exportCsv">导出 CSV</el-button>
    </div>
    <el-table :data="items" v-loading="loading" stripe>
      <el-table-column prop="campaign_id" label="ID" width="70" />
      <el-table-column prop="code" label="编码" width="120" />
      <el-table-column prop="name" label="活动" min-width="140" />
      <el-table-column prop="status" label="状态" width="100" />
      <el-table-column prop="coupons_issued" label="发券" width="80" />
      <el-table-column prop="coupons_used" label="核销" width="80" />
      <el-table-column label="核销率" width="90">
        <template #default="{ row }">
          {{ row.redeem_rate == null ? '—' : `${(row.redeem_rate * 100).toFixed(1)}%` }}
        </template>
      </el-table-column>
      <el-table-column prop="attributed_gross" label="归因实收" width="110" />
      <el-table-column prop="attributed_subsidy" label="补贴" width="90" />
      <el-table-column label="ROI" width="90">
        <template #default="{ row }">
          {{ row.roi == null ? '—' : row.roi }}
        </template>
      </el-table-column>
      <el-table-column prop="tasks_success" label="任务成功" width="90" />
    </el-table>
    <p class="hint">口径：归因实收来自 campaign_attributions（券优先）；ROI=(实收−补贴)/补贴；导出不含明文手机。</p>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import api from '@/api'

const loading = ref(false)
const items = ref<any[]>([])

async function load() {
  loading.value = true
  try {
    const res = await api.get('/marketing/reports/overview')
    items.value = res.data?.items || []
  } finally {
    loading.value = false
  }
}

function exportCsv() {
  const headers = [
    'campaign_id', 'code', 'name', 'status',
    'coupons_issued', 'coupons_used', 'redeem_rate',
    'attributed_gross', 'attributed_subsidy', 'roi', 'tasks_success',
  ]
  const lines = [headers.join(',')]
  for (const row of items.value) {
    const rate = row.redeem_rate == null ? '' : Number(row.redeem_rate).toFixed(4)
    lines.push([
      row.campaign_id,
      csvCell(row.code),
      csvCell(row.name),
      row.status,
      row.coupons_issued ?? 0,
      row.coupons_used ?? 0,
      rate,
      row.attributed_gross ?? '',
      row.attributed_subsidy ?? '',
      row.roi ?? '',
      row.tasks_success ?? 0,
    ].join(','))
  }
  const blob = new Blob(['\ufeff' + lines.join('\n')], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `marketing-report-${new Date().toISOString().slice(0, 10)}.csv`
  a.click()
  URL.revokeObjectURL(url)
}

function csvCell(v: unknown) {
  const s = String(v ?? '')
  if (/[",\n]/.test(s)) return `"${s.replace(/"/g, '""')}"`
  return s
}

onMounted(load)
</script>

<style scoped>
.page { padding: 8px; }
.toolbar { margin-bottom: 12px; display: flex; gap: 8px; }
.hint { margin-top: 12px; font-size: 12px; color: #888; }
</style>
