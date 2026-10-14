<template>
  <div v-loading="loading">
    <div class="toolbar">
      <el-select v-if="auth.isSuperAdmin" v-model="storeId" placeholder="选择门店" style="width:200px" @change="load">
        <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
      </el-select>
      <el-tag v-else type="info">本店库存覆盖</el-tag>
      <el-input v-model="keyword" clearable placeholder="餐品编号 / 名称" style="width:220px" @keyup.enter="reload"
        @clear="reload" />
      <el-button type="primary" @click="reload">查询</el-button>
      <span class="tip">有覆盖则下单扣本店覆盖库存；删除覆盖后回退餐品默认库存</span>
    </div>

    <el-alert v-if="truncated" type="warning" :closable="false" show-icon style="margin-top:10px"
      title="餐品数量超过 4000 条，当前仅加载前 4000 条，请用编号/名称搜索缩小范围。" />

    <el-table :data="items" border style="margin-top:12px">
      <el-table-column prop="product_no" label="编号" width="280" />
      <el-table-column prop="name" label="餐品" min-width="160" />
      <el-table-column prop="brand_stock" label="默认库存" width="100" />
      <el-table-column label="本店库存" width="140">
        <template #default="{ row }">
          <el-input-number v-model="row.stock" :min="0" :max="999999" size="small" />
        </template>
      </el-table-column>
      <el-table-column label="本店在售" width="100">
        <template #default="{ row }">
          <el-switch v-model="row.status" :active-value="1" :inactive-value="0" />
        </template>
      </el-table-column>
      <el-table-column label="覆盖" width="80">
        <template #default="{ row }">
          <el-tag size="small" :type="row.has_override ? 'warning' : 'info'">
            {{ row.has_override ? '是' : '否' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="160" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="saveRow(row)">保存</el-button>
          <el-button link type="danger" :disabled="!row.has_override" @click="clearRow(row)">清除</el-button>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api'
import { useStoreOptions } from '@/composables/useStoreOptions'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const loading = ref(false)
const { storeOptions: stores, loadStores: fetchStores } = useStoreOptions({ rethrow: true, clearOnError: false })
const storeId = ref<number | undefined>()
const items = ref<any[]>([])
const keyword = ref('')
const truncated = ref(false)

// 编辑型表格需行内直接修改，保留全量拉取；单店上限 20 页 × 200 = 4000 条
const FETCH_MAX_PAGES = 20
const PAGE_SIZE = 200

function reload() {
  return load()
}

async function loadStores() {
  await fetchStores()
  storeId.value = auth.user?.store_id || stores.value[0]?.id
}

async function load() {
  if (!storeId.value && auth.isSuperAdmin) {
    ElMessage.warning('请选择门店')
    return
  }
  loading.value = true
  truncated.value = false
  try {
    const kw = keyword.value.trim() || undefined
    const res = await api.get('/store-stocks', {
      params: { store_id: storeId.value, page: 1, page_size: PAGE_SIZE, keyword: kw },
    })
    const data = res.data || {}
    const all = [...(data.items || [])]
    const pages = Math.max(1, Number(data.total_pages || 1))
    for (let p = 2; p <= pages && p <= FETCH_MAX_PAGES; p++) {
      const more: any = await api.get('/store-stocks', {
        params: { store_id: storeId.value, page: p, page_size: PAGE_SIZE, keyword: kw },
      })
      all.push(...(more.data?.items || []))
    }
    if (Number(data.total || 0) > all.length) truncated.value = true
    items.value = all.map((it: any) => ({ ...it }))
  } catch (e: any) {
    ElMessage.error(e.message || '加载失败')
  } finally {
    loading.value = false
  }
}

async function saveRow(row: any) {
  try {
    await api.put(`/store-stocks/${row.product_id}`, {
      store_id: storeId.value,
      stock: row.stock,
      status: row.status,
    })
    ElMessage.success('已保存本店库存')
    load()
  } catch (e: any) {
    ElMessage.error(e.message || '保存失败')
  }
}

async function clearRow(row: any) {
  try {
    await api.delete(`/store-stocks/${row.product_id}`, { params: { store_id: storeId.value } })
    ElMessage.success('已清除覆盖')
    load()
  } catch (e: any) {
    ElMessage.error(e.message || '操作失败')
  }
}

onMounted(async () => {
  await loadStores()
  await load()
})
</script>

<style scoped>
.toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.tip {
  color: #999;
  font-size: 12px;
}
</style>
