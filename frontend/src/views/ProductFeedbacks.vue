<template>
  <div v-loading="loading">
    <el-form inline class="toolbar">
      <el-form-item v-if="auth.isSuperAdmin">
        <el-select v-model="storeFilter" placeholder="全部门店" clearable style="width:160px" @change="reload">
          <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
        </el-select>
      </el-form-item>
      <el-tag v-else-if="boundStoreName" type="info" style="margin-right:12px">门店：{{ boundStoreName }}</el-tag>
      <el-form-item>
        <el-select v-model="sentiment" placeholder="情感" clearable style="width:120px" @change="reload">
          <el-option v-for="(text, key) in FEEDBACK_SENTIMENT" :key="key" :label="text" :value="key" />
        </el-select>
      </el-form-item>
      <el-form-item>
        <el-input
          v-model="keyword"
          placeholder="餐品 / 订单号 / 顾客"
          clearable
          style="width:200px"
          @keyup.enter="reload"
          @clear="reload"
        />
      </el-form-item>
      <el-form-item>
        <el-date-picker
          v-model="dateRange"
          type="daterange"
          value-format="YYYY-MM-DD"
          start-placeholder="起"
          end-placeholder="止"
          style="width:240px"
          @change="reload"
        />
      </el-form-item>
      <el-button type="primary" @click="reload">查询</el-button>
      <el-button @click="resetFilters">重置</el-button>
    </el-form>

    <el-table :data="rows" border>
      <el-table-column label="时间" width="170">
        <template #default="{ row }">{{ row.created_at || '—' }}</template>
      </el-table-column>
      <el-table-column label="门店" width="110">
        <template #default="{ row }">{{ storeName(row.store_id) }}</template>
      </el-table-column>
      <el-table-column label="餐品" min-width="140" show-overflow-tooltip>
        <template #default="{ row }">
          <div>{{ row.product_name || '—' }}</div>
          <div class="sub">ID {{ row.product_id }}</div>
        </template>
      </el-table-column>
      <el-table-column label="顾客" min-width="160">
        <template #default="{ row }">
          <div>{{ formatCustomerNameLines(row).primary }}</div>
          <div v-if="formatCustomerNameLines(row).secondary" class="sub">{{ formatCustomerNameLines(row).secondary }}</div>
          <div class="sub">{{ row.customer_no || `ID ${row.customer_id}` }}</div>
        </template>
      </el-table-column>
      <el-table-column label="订单" min-width="160" show-overflow-tooltip>
        <template #default="{ row }">
          <router-link v-if="row.order_id" class="link" :to="`/orders/${row.order_id}`">
            {{ row.order_no || `#${row.order_id}` }}
          </router-link>
          <span v-else>—</span>
        </template>
      </el-table-column>
      <el-table-column label="情感" width="90">
        <template #default="{ row }">
          <el-tag size="small" :type="sentimentTag(row.sentiment)">
            {{ label(FEEDBACK_SENTIMENT, row.sentiment) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="标签" min-width="220">
        <template #default="{ row }">
          <el-tag
            v-for="v in (row.verdicts || [])"
            :key="v"
            size="small"
            class="tag-gap"
            :type="verdictTone(v)"
          >
            {{ label(FEEDBACK_VERDICT, v) }}
          </el-tag>
          <span v-if="!(row.verdicts || []).length">—</span>
        </template>
      </el-table-column>
      <el-table-column prop="remark" label="备注" min-width="140" show-overflow-tooltip />
    </el-table>

    <div class="pager">
      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :total="total"
        :page-sizes="[20, 50, 100]"
        layout="total, sizes, prev, pager, next"
        @current-change="load"
        @size-change="reload"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api'
import { useStoreOptions } from '@/composables/useStoreOptions'
import { useAuthStore } from '@/stores/auth'
import { formatCustomerNameLines } from '@/utils/customerName'
import {
  FEEDBACK_SENTIMENT,
  FEEDBACK_VERDICT,
  FEEDBACK_VERDICT_NEG,
  FEEDBACK_VERDICT_POS,
  label,
} from '@/utils/labels'

const auth = useAuthStore()
const loading = ref(false)
const rows = ref<any[]>([])
const { storeOptions: stores, loadStores: fetchStores } = useStoreOptions()
const storeFilter = ref<number | null>(null)
const boundStoreName = ref('')
const sentiment = ref('')
const keyword = ref('')
const dateRange = ref<string[] | null>(null)
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)

async function loadStores() {
  if (!(await fetchStores())) return
  if (!auth.isSuperAdmin && auth.user?.store_id) {
    const s = stores.value.find((x) => x.id === auth.user?.store_id)
    boundStoreName.value = s?.name || `门店#${auth.user.store_id}`
  }
}

function resetFilters() {
  storeFilter.value = null
  sentiment.value = ''
  keyword.value = ''
  dateRange.value = null
  reload()
}

function reload() {
  page.value = 1
  return load()
}

function storeName(sid: number | null) {
  if (!sid) return '—'
  return stores.value.find((s) => s.id === sid)?.name || `门店#${sid}`
}

function sentimentTag(s: string) {
  if (s === 'POSITIVE') return 'success'
  if (s === 'NEGATIVE') return 'danger'
  return 'info'
}

function verdictTone(code: string) {
  if (FEEDBACK_VERDICT_POS.has(code)) return 'success'
  if (FEEDBACK_VERDICT_NEG.has(code)) return 'danger'
  return 'info'
}

async function load() {
  loading.value = true
  try {
    const params: Record<string, any> = { page: page.value, page_size: pageSize.value }
    if (auth.isSuperAdmin && storeFilter.value) params.store_id = storeFilter.value
    if (sentiment.value) params.sentiment = sentiment.value
    if (keyword.value.trim()) params.keyword = keyword.value.trim()
    if (dateRange.value?.length === 2) {
      params.date_from = dateRange.value[0]
      params.date_to = dateRange.value[1]
    }
    const res: any = await api.get('/product-feedbacks', { params })
    const list = res.data?.items || []
    if (list.length === 0 && page.value > 1) {
      page.value -= 1
      loading.value = false
      await load()
      return
    }
    rows.value = list
    total.value = Number(res.data?.total || 0)
  } catch (e: any) {
    ElMessage.error(e.message || '加载失败')
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await loadStores()
  await load()
})
</script>

<style scoped>
.toolbar {
  margin-bottom: 8px;
}
.pager {
  margin-top: 16px;
  display: flex;
  justify-content: flex-end;
}
.sub {
  color: #909399;
  font-size: 12px;
}
.tag-gap {
  margin-right: 4px;
  margin-bottom: 2px;
}
.link {
  color: var(--el-color-primary);
  text-decoration: none;
}
.link:hover {
  text-decoration: underline;
}
</style>
