<template>
  <div class="maps-page">
    <div class="toolbar">
      <el-select v-model="syncStoreId" placeholder="门店" style="width: 150px" @change="reloadMaps">
        <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
      </el-select>
      <el-select v-model="syncPlatform" style="width: 100px" @change="reloadMaps">
        <el-option label="美团" value="MEITUAN" />
        <el-option label="饿了么" value="ELEME" />
      </el-select>
      <el-input v-model="mapKeyword" placeholder="平台SKU / 品名 / 菜品码" clearable style="width: 210px"
        @keyup.enter="reloadMaps" @clear="reloadMaps" />
      <el-button type="primary" @click="syncMenuBatch">批量推送菜单</el-button>
      <el-button type="success" @click="setPlatformShelf(true)">平台上架</el-button>
      <el-button type="warning" @click="setPlatformShelf(false)">平台下架</el-button>
      <el-button @click="openCategoryDialog">平台分类映射</el-button>
      <el-divider direction="vertical" />
      <el-button @click="syncMenu">同步菜单</el-button>
      <el-button @click="syncPrices">同步价格</el-button>
      <el-button @click="syncStock">同步库存</el-button>
      <el-button @click="openMapDialog">新增映射</el-button>
    </div>
    <el-table :data="maps" size="small" stripe>
      <el-table-column prop="platform" label="平台" width="80">
        <template #default="{ row }">{{ platformName(row.platform) }}</template>
      </el-table-column>
      <el-table-column prop="platform_sku_id" label="平台SKU" min-width="120" show-overflow-tooltip />
      <el-table-column prop="platform_sku_name" label="平台品名" min-width="120" show-overflow-tooltip />
      <el-table-column prop="product_id" label="本系统ID" width="90" />
      <el-table-column prop="product_name" label="本系统餐品" min-width="120" show-overflow-tooltip />
      <el-table-column prop="app_food_code" label="菜品码" width="100" show-overflow-tooltip />
      <el-table-column label="状态" width="70">
        <template #default="{ row }">{{ row.status === 1 ? '启用' : '停用' }}</template>
      </el-table-column>
    </el-table>
    <div class="pager">
      <el-pagination v-model:current-page="mapPager.page" v-model:page-size="mapPager.pageSize" :total="mapPager.total"
        :page-sizes="[20, 50, 100, 200]" layout="total, sizes, prev, pager, next" background small
        @current-change="loadMaps" @size-change="reloadMaps" />
    </div>

    <!-- 映射弹窗 -->
    <el-dialog v-model="showMapDialog" title="新增/更新餐品映射" width="480px">
      <el-form label-width="110px">
        <el-form-item v-if="auth.isSuperAdmin" label="归属门店" required>
          <el-select v-model="mapForm.store_id" placeholder="选择门店" style="width: 100%">
            <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="平台">
          <el-select v-model="mapForm.platform" style="width: 100%">
            <el-option label="美团" value="MEITUAN" />
            <el-option label="饿了么" value="ELEME" />
          </el-select>
        </el-form-item>
        <el-form-item label="平台 SKU" required>
          <el-input v-model="mapForm.platform_sku_id" />
        </el-form-item>
        <el-form-item label="平台品名">
          <el-input v-model="mapForm.platform_sku_name" />
        </el-form-item>
        <el-form-item label="本系统餐品ID" required>
          <el-input-number v-model="mapForm.product_id" :min="1" style="width: 100%" />
        </el-form-item>
        <el-form-item label="菜品码">
          <el-input v-model="mapForm.app_food_code" placeholder="默认取餐品编号" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showMapDialog = false">取消</el-button>
        <el-button type="primary" @click="saveMap">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="categoryDialog" title="平台分类映射" width="640px">
      <el-form inline style="margin-bottom: 8px">
        <el-form-item label="平台">
          <el-select v-model="categoryForm.platform" style="width: 110px">
            <el-option label="美团" value="MEITUAN" />
            <el-option label="饿了么" value="ELEME" />
          </el-select>
        </el-form-item>
        <el-form-item label="本地分类ID">
          <el-input-number v-model="categoryForm.local_category_id" :min="1" controls-position="right" />
        </el-form-item>
        <el-form-item label="平台分类名">
          <el-input v-model="categoryForm.platform_category_name" placeholder="如：招牌面食" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="saveCategoryMap">添加映射</el-button>
        </el-form-item>
      </el-form>
      <p class="muted" style="font-size: 12px; margin: 0 0 8px">
        未配置映射时默认按本地分类名推送；本地分类ID可在「菜品-分类管理」中查看。store 级映射仅对当前门店生效。
      </p>
      <el-table :data="categoryMaps" size="small" border max-height="280">
        <el-table-column prop="platform" label="平台" width="90">
          <template #default="{ row }">{{ platformName(row.platform) }}</template>
        </el-table-column>
        <el-table-column prop="local_category_id" label="本地分类ID" width="100" />
        <el-table-column prop="local_category_name" label="本地分类" min-width="120" />
        <el-table-column prop="platform_category_name" label="平台分类名" min-width="120" />
        <el-table-column label="范围" width="80">
          <template #default="{ row }">{{ row.store_id ? '门店' : '总部' }}</template>
        </el-table-column>
      </el-table>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api'
import { useStoreOptions } from '@/composables/useStoreOptions'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const { storeOptions: stores, loadStores: fetchStores } = useStoreOptions()
const maps = ref<any[]>([])
const syncStoreId = ref<number | undefined>()
const syncPlatform = ref('MEITUAN')
const mapKeyword = ref('')
const mapPager = reactive({ page: 1, pageSize: 20, total: 0 })
const showMapDialog = ref(false)
const mapForm = reactive<{
  platform: string
  store_id?: number
  platform_sku_id: string
  platform_sku_name: string
  product_id: number
  app_food_code: string
}>({
  platform: 'MEITUAN',
  store_id: undefined,
  platform_sku_id: '',
  platform_sku_name: '',
  product_id: 0,
  app_food_code: '',
})

const categoryDialog = ref(false)
const categoryMaps = ref<any[]>([])
const categoryForm = reactive({
  platform: 'MEITUAN',
  local_category_id: undefined as number | undefined,
  platform_category_name: '',
  platform_category_code: '',
})

function platformName(p: string) {
  return p === 'MEITUAN' ? '美团' : p === 'ELEME' ? '饿了么' : p || '-'
}

async function loadStores() {
  if (!(await fetchStores())) return
  if (syncStoreId.value == null) {
    const defaultSid = auth.user?.store_id || stores.value[0]?.id
    if (defaultSid) syncStoreId.value = Number(defaultSid)
  }
}

async function loadMaps() {
  const params: Record<string, any> = {
    page: mapPager.page,
    page_size: mapPager.pageSize,
    platform: syncPlatform.value,
  }
  if (syncStoreId.value) params.store_id = syncStoreId.value
  if (mapKeyword.value.trim()) params.keyword = mapKeyword.value.trim()
  const res = await api.get('/delivery/product-maps', { params })
  const data = res.data
  maps.value = Array.isArray(data) ? data : data?.items || []
  mapPager.total = Number(data?.total || 0)
}

function reloadMaps() {
  mapPager.page = 1
  return loadMaps()
}

function openMapDialog() {
  mapForm.platform = syncPlatform.value
  // 默认归属当前列表选中的门店（门店账号由后端强制本店）
  mapForm.store_id = syncStoreId.value
  mapForm.platform_sku_id = ''
  mapForm.platform_sku_name = ''
  mapForm.product_id = 0
  mapForm.app_food_code = ''
  showMapDialog.value = true
}

async function saveMap() {
  if (!mapForm.platform_sku_id?.trim()) {
    ElMessage.warning('请填写平台 SKU')
    return
  }
  if (!mapForm.product_id || mapForm.product_id <= 0) {
    ElMessage.warning('请填写有效的本系统餐品 ID')
    return
  }
  if (auth.isSuperAdmin && !mapForm.store_id) {
    ElMessage.warning('请选择归属门店')
    return
  }
  try {
    await api.post('/delivery/product-maps', mapForm)
    ElMessage.success('映射已保存')
    showMapDialog.value = false
    loadMaps()
  } catch (e: any) {
    ElMessage.error(e.message || '保存失败')
  }
}

async function syncMenu() {
  try {
    const res = await api.post('/delivery/sync/menu', null, { params: { platform: syncPlatform.value } })
    ElMessage.success(`同步 ${res.data.synced}/${res.data.total} 个餐品`)
  } catch (e: any) {
    ElMessage.error(e.message || '同步失败')
  }
}

async function syncPrices() {
  try {
    const res = await api.post('/delivery/sync/prices', null, { params: { platform: syncPlatform.value } })
    ElMessage.success(`更新 ${res.data.updated} 个价格`)
  } catch (e: any) {
    ElMessage.error(e.message || '同步失败')
  }
}

async function syncStock() {
  try {
    const res = await api.post('/delivery/sync/stock', null, { params: { platform: syncPlatform.value } })
    ElMessage.success(`更新 ${res.data.updated} 个库存`)
  } catch (e: any) {
    ElMessage.error(e.message || '同步失败')
  }
}

async function syncMenuBatch() {
  if (!syncStoreId.value) {
    ElMessage.warning('请先选择门店')
    return
  }
  try {
    const res = await api.post('/delivery/sync/menu-batch', null, {
      params: { store_id: syncStoreId.value, platform: syncPlatform.value },
    })
    ElMessage.success(`菜单推送完成：${res.data.synced ?? 0}/${res.data.total ?? 0}`)
    loadMaps()
  } catch (e: any) {
    ElMessage.error(e.message || '推送失败')
  }
}

async function setPlatformShelf(onShelf: boolean) {
  if (!syncStoreId.value) {
    ElMessage.warning('请先选择门店')
    return
  }
  try {
    const res = await api.post('/delivery/sync/shelf', {
      store_id: syncStoreId.value,
      platform: syncPlatform.value,
      on_shelf: onShelf,
    })
    ElMessage.success(`已${onShelf ? '上架' : '下架'} ${res.data?.updated ?? 0} 个菜品`)
  } catch (e: any) {
    ElMessage.error(e.message || '操作失败')
  }
}

async function loadCategoryMaps() {
  try {
    const params: Record<string, any> = {}
    if (syncStoreId.value) params.store_id = syncStoreId.value
    const res = await api.get('/delivery/category-maps', { params })
    categoryMaps.value = res.data?.items || []
  } catch (e: any) {
    ElMessage.error(e.message || '分类映射加载失败')
  }
}

function openCategoryDialog() {
  categoryDialog.value = true
  loadCategoryMaps()
}

async function saveCategoryMap() {
  if (!categoryForm.local_category_id) {
    ElMessage.warning('请填写本地分类ID')
    return
  }
  try {
    await api.post('/delivery/category-maps', {
      ...categoryForm,
      store_id: syncStoreId.value || null,
    })
    ElMessage.success('映射已保存')
    await loadCategoryMaps()
    categoryForm.local_category_id = undefined
    categoryForm.platform_category_name = ''
    categoryForm.platform_category_code = ''
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || e.message || '保存失败')
  }
}

onMounted(async () => {
  await loadStores()
  await loadMaps()
})
</script>

<style scoped>
.maps-page {
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

.pager {
  margin-top: 12px;
  display: flex;
  justify-content: flex-end;
}
</style>
