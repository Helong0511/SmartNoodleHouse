<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2>营销报表</h2>
        <p class="sub">按活动汇总发券、核销、归因实收与 ROI；可导出 CSV 做店长复盘。</p>
      </div>
      <div class="header-actions">
        <el-button @click="goCampaigns">营销活动</el-button>
        <el-button @click="goSocial">社会贡献看板</el-button>
        <el-button type="primary" @click="load" :loading="loading">刷新报表</el-button>
        <el-button :disabled="!items.length" @click="exportCsv">导出 CSV</el-button>
      </div>
    </div>

    <el-alert
      type="info"
      show-icon
      :closable="false"
      class="tip"
      title="口径说明"
      description="发券/核销来自活动任务与券核销；归因实收与补贴来自 campaign_attributions（订单用券优先归因到对应活动）。ROI = (归因实收 − 补贴) / 补贴；补贴为 0 时 ROI 显示为 —。导出不含明文手机。"
    />
    <el-alert
      type="warning"
      show-icon
      :closable="false"
      class="tip"
      title="与社会贡献区分"
      description="本页只统计营销活动（发券归因）。环卫/骑手/长者等身份折扣的人次与补贴在「社会贡献 → 本月看板」，不要混在同一张 ROI 表里解读。"
    />

    <el-empty v-if="!loading && !items.length" description="暂无活动数据；发布并产生发券/核销后才会出现在此" />
    <el-table v-else :data="items" v-loading="loading" stripe>
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
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import api from '@/api'

const router = useRouter()
const loading = ref(false)
const items = ref<any[]>([])

function goCampaigns() {
  router.push('/marketing/campaigns')
}
function goSocial() {
  router.push('/social-benefits')
}

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
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 12px;
}
.page-header h2 { margin: 0 0 4px; font-size: 18px; }
.sub { margin: 0; font-size: 13px; color: #888; line-height: 1.5; max-width: 640px; }
.header-actions { display: flex; gap: 8px; flex-wrap: wrap; flex-shrink: 0; }
.tip { margin-bottom: 10px; }
</style>
