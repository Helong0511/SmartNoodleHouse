<template>
  <div class="store-info-page">
    <el-card shadow="never">
      <template #header>
        <div style="display: flex; align-items: center; justify-content: space-between">
          <span style="font-weight: 600">门店信息</span>
          <el-button type="primary" size="small" @click="openCreateStore">新建门店</el-button>
        </div>
      </template>
      <el-table :data="stores" border size="small">
        <el-table-column prop="store_no" label="编号" width="100" />
        <el-table-column label="名称" min-width="140">
          <template #default="{ row }">
            <el-input v-if="storeEdits[row.id]" v-model="storeEdits[row.id].name" size="small" maxlength="100" />
            <span v-else>{{ row.name }}</span>
          </template>
        </el-table-column>
        <el-table-column label="地址" min-width="180">
          <template #default="{ row }">
            <el-input v-if="storeEdits[row.id]" v-model="storeEdits[row.id].address" size="small" maxlength="255" />
            <span v-else>{{ row.address || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="电话" width="140">
          <template #default="{ row }">
            <el-input v-if="storeEdits[row.id]" v-model="storeEdits[row.id].phone" size="small" maxlength="20" />
            <span v-else>{{ row.phone || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="纬度" width="110">
          <template #default="{ row }">
            <el-input v-if="storeEdits[row.id]" v-model="storeEdits[row.id].latitude" size="small" placeholder="如 31.23" />
            <span v-else>{{ row.latitude ?? '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="经度" width="110">
          <template #default="{ row }">
            <el-input v-if="storeEdits[row.id]" v-model="storeEdits[row.id].longitude" size="small" placeholder="如 121.47" />
            <span v-else>{{ row.longitude ?? '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-switch v-if="storeEdits[row.id]" v-model="storeEdits[row.id].status" :active-value="1" :inactive-value="0" />
            <el-tag v-else :type="row.status === 1 ? 'success' : 'info'" size="small">{{ row.status === 1 ? '营业' : '停用' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="140" fixed="right">
          <template #default="{ row }">
            <template v-if="storeEdits[row.id]">
              <el-button link type="primary" :loading="storeSaving === row.id" @click="saveStore(row.id)">保存</el-button>
              <el-button link @click="cancelStoreEdit(row)">取消</el-button>
            </template>
            <el-button v-else link type="primary" @click="startStoreEdit(row)">编辑</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="createStoreVisible" title="新建门店" width="420px" destroy-on-close>
      <el-form :model="createStoreForm" label-width="80px">
        <el-form-item label="编号" required>
          <el-input v-model="createStoreForm.store_no" placeholder="如 SNH002" maxlength="40" />
        </el-form-item>
        <el-form-item label="名称" required>
          <el-input v-model="createStoreForm.name" placeholder="门店名称" maxlength="100" />
        </el-form-item>
        <el-form-item label="地址">
          <el-input v-model="createStoreForm.address" maxlength="255" />
        </el-form-item>
        <el-form-item label="电话">
          <el-input v-model="createStoreForm.phone" maxlength="20" />
        </el-form-item>
        <el-form-item label="纬度">
          <el-input v-model="createStoreForm.latitude" placeholder="可选，天气口味用" />
        </el-form-item>
        <el-form-item label="经度">
          <el-input v-model="createStoreForm.longitude" placeholder="可选，天气口味用" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createStoreVisible = false">取消</el-button>
        <el-button type="primary" :loading="creatingStore" @click="createStore">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api'

const stores = ref<any[]>([])
const storeEdits = reactive<Record<number, any>>({})
const storeSaving = ref<number | null>(null)
const createStoreVisible = ref(false)
const creatingStore = ref(false)
const createStoreForm = reactive({
  store_no: '',
  name: '',
  address: '',
  phone: '',
  latitude: '' as string | number,
  longitude: '' as string | number,
})

function startStoreEdit(row: any) {
  storeEdits[row.id] = {
    name: row.name,
    address: row.address || '',
    phone: row.phone || '',
    latitude: row.latitude ?? '',
    longitude: row.longitude ?? '',
    status: row.status,
  }
}

function cancelStoreEdit(row: any) {
  delete storeEdits[row.id]
}

async function saveStore(id: number) {
  const edit = storeEdits[id]
  if (!edit?.name?.trim()) {
    ElMessage.warning('门店名称不能为空')
    return
  }
  storeSaving.value = id
  try {
    const latRaw = String(edit.latitude || '').trim()
    const lngRaw = String(edit.longitude || '').trim()
    await api.put(`/settings/stores/${id}`, {
      name: edit.name.trim(),
      address: edit.address?.trim() || null,
      phone: edit.phone?.trim() || null,
      latitude: latRaw === '' ? null : Number(latRaw),
      longitude: lngRaw === '' ? null : Number(lngRaw),
      status: edit.status,
    })
    ElMessage.success('门店已更新')
    delete storeEdits[id]
    await loadStores()
  } catch (e: any) {
    ElMessage.error(e.message || '保存失败')
  } finally {
    storeSaving.value = null
  }
}

function openCreateStore() {
  Object.assign(createStoreForm, {
    store_no: '', name: '', address: '', phone: '', latitude: '', longitude: '',
  })
  createStoreVisible.value = true
}

async function createStore() {
  if (!createStoreForm.store_no.trim() || !createStoreForm.name.trim()) {
    ElMessage.warning('请填写门店编号和名称')
    return
  }
  creatingStore.value = true
  try {
    const latRaw = String(createStoreForm.latitude || '').trim()
    const lngRaw = String(createStoreForm.longitude || '').trim()
    await api.post('/settings/stores', {
      store_no: createStoreForm.store_no.trim(),
      name: createStoreForm.name.trim(),
      address: createStoreForm.address.trim() || null,
      phone: createStoreForm.phone.trim() || null,
      latitude: latRaw === '' ? null : Number(latRaw),
      longitude: lngRaw === '' ? null : Number(lngRaw),
    })
    ElMessage.success('门店已创建')
    createStoreVisible.value = false
    await loadStores()
  } catch (e: any) {
    ElMessage.error(e.message || '创建失败')
  } finally {
    creatingStore.value = false
  }
}

async function loadStores() {
  try {
    const res = await api.get('/settings/stores/options')
    stores.value = res.data.items || []
  } catch {
    stores.value = []
  }
}

onMounted(() => {
  loadStores()
})
</script>

<style scoped>
.store-info-page {
  min-height: 400px;
}
</style>
