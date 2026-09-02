<template>
  <div class="settlement-page">
    <div class="toolbar">
      <el-date-picker v-model="date" type="date" value-format="YYYY-MM-DD" placeholder="选择日期" size="large" @change="load" />
      <el-select v-if="auth.isSuperAdmin" v-model="storeId" placeholder="门店" size="large" style="width:200px" @change="load">
        <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
      </el-select>
      <el-button type="primary" size="large" :loading="loading" @click="load">刷新</el-button>
    </div>

    <div v-if="loading" class="tip">加载中…</div>
    <template v-else-if="report">
      <div class="summary">
        <div class="sum-item"><span>班次</span><b>{{ report.shift_count }}</b></div>
        <div class="sum-item"><span>备用金合计</span><b>¥{{ report.total_opening_float }}</b></div>
        <div class="sum-item"><span>现金</span><b>¥{{ report.total_cash }}</b></div>
        <div class="sum-item"><span>微信</span><b>¥{{ report.total_wechat }}</b></div>
        <div class="sum-item"><span>支付宝</span><b>¥{{ report.total_alipay }}</b></div>
        <div class="sum-item highlight"><span>收款总计</span><b>¥{{ report.total_amount }}</b></div>
      </div>

      <el-table :data="report.shifts || []" stripe style="width:100%;margin-top:16px">
        <el-table-column prop="operator_name" label="收银员" min-width="100" />
        <el-table-column prop="opening_float" label="备用金" width="100">
          <template #default="{ row }">¥{{ row.opening_float }}</template>
        </el-table-column>
        <el-table-column prop="total_cash" label="现金" width="100">
          <template #default="{ row }">¥{{ row.total_cash }}</template>
        </el-table-column>
        <el-table-column prop="total_wechat" label="微信" width="100">
          <template #default="{ row }">¥{{ row.total_wechat }}</template>
        </el-table-column>
        <el-table-column prop="total_alipay" label="支付宝" width="100">
          <template #default="{ row }">¥{{ row.total_alipay }}</template>
        </el-table-column>
        <el-table-column prop="total_amount" label="合计" width="110">
          <template #default="{ row }"><b>¥{{ row.total_amount }}</b></template>
        </el-table-column>
        <el-table-column prop="closing_cash" label="交班现金" width="110">
          <template #default="{ row }">{{ row.closing_cash != null ? `¥${row.closing_cash}` : '—' }}</template>
        </el-table-column>
        <el-table-column prop="cash_difference" label="长短款" width="100">
          <template #default="{ row }">
            <span v-if="row.cash_difference != null" :class="{ bad: +row.cash_difference < 0 }">
              ¥{{ row.cash_difference }}
            </span>
            <span v-else>—</span>
          </template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="row.status === 'OPEN' ? 'warning' : 'success'" size="small">
              {{ row.status === 'OPEN' ? '营业中' : '已交班' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="opened_at" label="开班" min-width="150" show-overflow-tooltip />
        <el-table-column prop="closed_at" label="交班" min-width="150" show-overflow-tooltip />
      </el-table>
      <div v-if="!(report.shifts || []).length" class="empty">当日暂无收银班次记录</div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import api from '@/api'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const loading = ref(false)
const report = ref<any>(null)
const date = ref(new Date().toISOString().slice(0, 10))
const storeId = ref<number | null>(null)
const stores = ref<any[]>([])

async function loadStores() {
  if (!auth.isSuperAdmin) {
    storeId.value = auth.user?.store_id ?? null
    return
  }
  const res = await api.get('/orders/kitchen/stores')
  stores.value = res.data?.items || []
  if (!storeId.value && stores.value.length) storeId.value = stores.value[0].id
}

async function load() {
  loading.value = true
  try {
    const params: Record<string, string | number> = { date: date.value }
    if (storeId.value) params.store_id = storeId.value
    const res = await api.get('/pos/settlement/daily', { params })
    report.value = res.data
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  if (!auth.user) await auth.fetchMe()
  await loadStores()
  await load()
})
</script>

<style scoped>
.settlement-page { padding: 4px; }
.toolbar { display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 16px; }
.tip, .empty { text-align: center; padding: 40px; color: #999; }
.summary {
  display: flex; flex-wrap: wrap; gap: 12px;
  background: #fafafa; border: 1px solid #eee; border-radius: 12px; padding: 16px;
}
.sum-item { min-width: 120px; }
.sum-item span { display: block; font-size: 13px; color: #888; }
.sum-item b { font-size: 20px; }
.sum-item.highlight b { color: #ff6b35; font-size: 24px; }
.bad { color: #f5222d; font-weight: 700; }
</style>
