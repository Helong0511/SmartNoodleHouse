<template>
  <div v-loading="loading">
    <el-form inline class="toolbar">
      <el-form-item v-if="auth.isSuperAdmin">
        <el-select
          v-model="storeFilter"
          placeholder="全部门店"
          clearable
          style="width:160px"
          @change="reload"
        >
          <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
        </el-select>
      </el-form-item>
      <el-tag v-else-if="boundStoreName" type="info" style="margin-right:12px">门店：{{ boundStoreName }}</el-tag>
      <el-form-item>
        <el-select v-model="entryType" placeholder="分录类型" clearable style="width:120px" @change="reload">
          <el-option label="收款 SALE" value="SALE" />
          <el-option label="退款 REFUND" value="REFUND" />
        </el-select>
      </el-form-item>
      <el-form-item>
        <el-select
          v-model="channel"
          clearable
          filterable
          allow-create
          default-first-option
          placeholder="渠道"
          style="width:130px"
          @change="reload"
        >
          <el-option label="微信" value="WECHAT" />
          <el-option label="支付宝" value="ALIPAY" />
          <el-option label="现金" value="CASH" />
          <el-option label="组合支付" value="MIXED" />
          <el-option label="美团" value="MEITUAN" />
          <el-option label="饿了么" value="ELEME" />
        </el-select>
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
      <el-table-column prop="id" label="ID" width="80" />
      <el-table-column label="门店" width="110">
        <template #default="{ row }">{{ storeName(row.store_id) }}</template>
      </el-table-column>
      <el-table-column prop="entry_type" label="类型" width="100" />
      <el-table-column prop="channel" label="渠道" width="110" />
      <el-table-column prop="provider" label="通道" width="100" />
      <el-table-column prop="amount" label="金额" width="110">
        <template #default="{ row }">¥{{ row.amount }}</template>
      </el-table-column>
      <el-table-column prop="order_id" label="订单" width="100" />
      <el-table-column prop="payment_id" label="支付" width="100" />
      <el-table-column prop="refund_id" label="退款" width="100" />
      <el-table-column prop="remark" label="备注" min-width="160" show-overflow-tooltip />
      <el-table-column prop="created_at" label="时间" min-width="170" />
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
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const loading = ref(false)
const rows = ref<any[]>([])
const stores = ref<{ id: number; name: string }[]>([])
const storeFilter = ref<number | null>(null)
const boundStoreName = ref('')
const entryType = ref('')
const channel = ref('')
const dateRange = ref<string[] | null>(null)
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)

async function loadStores() {
  try {
    const res: any = await api.get('/settings/stores/options')
    stores.value = res.data?.items || []
    if (!auth.isSuperAdmin && auth.user?.store_id) {
      const s = stores.value.find((x) => x.id === auth.user?.store_id)
      boundStoreName.value = s?.name || `门店#${auth.user.store_id}`
    }
  } catch {
    stores.value = []
  }
}

function resetFilters() {
  storeFilter.value = null
  entryType.value = ''
  channel.value = ''
  dateRange.value = null
  reload()
}

function reload() {
  page.value = 1
  return load()
}

function storeName(sid: number) {
  if (!sid) return '—'
  return stores.value.find((s) => s.id === sid)?.name || `门店#${sid}`
}

async function load() {
  loading.value = true
  try {
    const params: Record<string, any> = { page: page.value, page_size: pageSize.value }
    if (auth.isSuperAdmin && storeFilter.value) params.store_id = storeFilter.value
    if (entryType.value) params.entry_type = entryType.value
    if (channel.value.trim()) params.channel = channel.value.trim()
    if (dateRange.value?.length === 2) {
      params.date_from = `${dateRange.value[0]}T00:00:00`
      params.date_to = `${dateRange.value[1]}T23:59:59`
    }
    const res: any = await api.get('/financial-ledger', { params })
    const list = res.data?.items || []
    // 退款红冲等场景导致末页变空时自动回退
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
.toolbar { margin-bottom: 12px; }
.pager { margin-top: 16px; display: flex; justify-content: flex-end; }
</style>
