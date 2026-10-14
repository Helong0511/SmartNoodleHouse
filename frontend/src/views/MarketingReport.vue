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
      description="发券/核销来自活动任务与券核销；归因实收与补贴来自营销归因台账（订单用券优先归因到对应活动）。ROI = (归因实收 − 补贴) / 补贴；补贴为 0 时 ROI 显示为 —。导出不含明文手机。"
    />
    <el-alert
      type="warning"
      show-icon
      :closable="false"
      class="tip"
      title="与社会贡献区分"
      description="本页只统计营销活动（发券归因）。环卫/骑手/长者/教师/医护等身份折扣的人次与补贴在「社会贡献 → 本月看板」，不要混在同一张 ROI 表里解读。"
    />

    <div class="filter-bar">
      <el-select
        v-if="auth.isSuperAdmin"
        v-model="filterStoreId"
        clearable
        placeholder="门店（空=全部/含总部）"
        style="width: 200px"
        @change="load"
      >
        <el-option v-for="s in storeOptions" :key="s.id" :label="s.name" :value="s.id" />
      </el-select>
      <el-tag v-else-if="boundStoreName" type="info">门店：{{ boundStoreName }}</el-tag>
    </div>

    <el-empty v-if="!loading && !items.length" description="暂无活动数据；发布并产生发券/核销后才会出现在此" />
    <el-table v-else :data="items" v-loading="loading" stripe>
      <el-table-column prop="campaign_id" label="ID" width="70" />
      <el-table-column label="门店" width="120" show-overflow-tooltip>
        <template #default="{ row }">{{ storeLabel(row.store_id) }}</template>
      </el-table-column>
      <el-table-column prop="code" label="编码" width="120" />
      <el-table-column prop="name" label="活动" min-width="140" />
      <el-table-column label="状态" width="100">
        <template #default="{ row }">{{ label(MARKETING_CAMPAIGN_STATUS, row.status) }}</template>
      </el-table-column>
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
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import api from '@/api'
import { useAuthStore } from '@/stores/auth'
import { useStoreOptions } from '@/composables/useStoreOptions'
import { MARKETING_CAMPAIGN_STATUS, label } from '@/utils/labels'

const router = useRouter()
const auth = useAuthStore()
const { storeOptions, loadStores } = useStoreOptions()
const loading = ref(false)
const items = ref<any[]>([])
const filterStoreId = ref<number | undefined>()

const boundStoreName = computed(() => {
  if (auth.isSuperAdmin) return ''
  const sid = auth.user?.store_id
  const s = storeOptions.value.find((x) => x.id === sid)
  return s?.name || auth.user?.store_name || (sid ? `门店#${sid}` : '')
})

function storeLabel(id: number | null | undefined) {
  if (id == null) return '总部'
  const s = storeOptions.value.find((x) => x.id === id)
  return s?.name || `#${id}`
}

function goCampaigns() {
  router.push('/marketing/campaigns')
}
function goSocial() {
  router.push('/social-benefits')
}

async function load() {
  loading.value = true
  try {
    await loadStores()
    if (!auth.isSuperAdmin && auth.user?.store_id) filterStoreId.value = auth.user.store_id
    const sid = auth.isSuperAdmin ? filterStoreId.value : auth.user?.store_id
    const res = await api.get('/marketing/reports/overview', {
      params: sid ? { store_id: sid } : {},
    })
    items.value = (res.data?.items || []).map((row: any) => ({
      ...row,
      store_id: row.store_id ?? null,
    }))
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
.filter-bar { margin-bottom: 12px; display: flex; gap: 8px; align-items: center; }
</style>
