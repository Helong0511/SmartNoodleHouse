<template>
  <div>
    <el-tabs v-model="tab">
      <el-tab-pane label="外卖订单" name="orders">
        <el-select v-model="platformFilter" placeholder="平台" clearable style="width:120px;margin-right:8px" @change="loadOrders">
          <el-option label="美团" value="MEITUAN" />
          <el-option label="饿了么" value="ELEME" />
        </el-select>
        <el-button @click="loadOrders">刷新</el-button>
        <el-table :data="orders" style="margin-top:12px" size="small">
          <el-table-column prop="platform" label="平台" width="80" />
          <el-table-column prop="platform_day_seq" label="流水号" width="80" />
          <el-table-column prop="platform_order_id" label="平台单号" min-width="140" />
          <el-table-column prop="delivery_status" label="接单状态" width="90" />
          <el-table-column prop="logistics_status" label="配送状态" width="90" />
          <el-table-column prop="refund_status" label="退款" width="80" />
          <el-table-column prop="urge_count" label="催单" width="60" />
          <el-table-column prop="recipient_name" label="顾客" width="80" />
          <el-table-column prop="recipient_address" label="地址" min-width="160" show-overflow-tooltip />
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="退款处理" name="refunds">
        <el-button @click="loadRefunds">刷新</el-button>
        <el-table :data="refunds" style="margin-top:12px" size="small">
          <el-table-column prop="platform" label="平台" width="80" />
          <el-table-column prop="order_id" label="订单ID" width="80" />
          <el-table-column prop="amount" label="金额" width="80" />
          <el-table-column prop="reason" label="原因" min-width="160" show-overflow-tooltip />
          <el-table-column prop="status" label="状态" width="90" />
          <el-table-column label="操作" width="180">
            <template #default="{ row }">
              <template v-if="row.status === 'PENDING'">
                <el-button size="small" type="success" @click="handleRefund(row.id, true)">同意</el-button>
                <el-button size="small" type="danger" @click="handleRefund(row.id, false)">拒绝</el-button>
              </template>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="门店绑定" name="bindings">
        <el-form inline>
          <el-form-item label="平台">
            <el-select v-model="bindingForm.platform" style="width:120px">
              <el-option label="美团" value="MEITUAN" />
              <el-option label="饿了么" value="ELEME" />
            </el-select>
          </el-form-item>
          <el-form-item label="平台门店ID">
            <el-input v-model="bindingForm.platform_shop_id" placeholder="app_poi_code / shopId" />
          </el-form-item>
          <el-form-item label="门店名称">
            <el-input v-model="bindingForm.platform_shop_name" />
          </el-form-item>
          <el-form-item label="Access Token">
            <el-input v-model="bindingForm.access_token" placeholder="可选" style="width:200px" />
          </el-form-item>
          <el-form-item label="Refresh Token">
            <el-input v-model="bindingForm.refresh_token" placeholder="饿了么 OAuth" style="width:200px" />
          </el-form-item>
          <el-button type="primary" @click="saveBinding">保存绑定</el-button>
        </el-form>
        <el-table :data="bindings" style="margin-top:12px" size="small">
          <el-table-column prop="platform" label="平台" width="80" />
          <el-table-column prop="platform_shop_id" label="平台门店ID" />
          <el-table-column prop="platform_shop_name" label="名称" />
          <el-table-column prop="has_token" label="已授权" width="80">
            <template #default="{ row }">{{ row.has_token ? '是' : '否' }}</template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="商品映射" name="maps">
        <el-button type="primary" @click="showMapDialog = true">新增映射</el-button>
        <el-button @click="syncMenu">同步菜单</el-button>
        <el-select v-model="syncPlatform" style="width:100px;margin-left:8px">
          <el-option label="美团" value="MEITUAN" />
          <el-option label="饿了么" value="ELEME" />
        </el-select>
        <el-button @click="syncPrices">同步价格</el-button>
        <el-button @click="syncStock">同步库存</el-button>
        <el-table :data="maps" style="margin-top:12px" size="small">
          <el-table-column prop="platform" label="平台" width="80" />
          <el-table-column prop="platform_sku_id" label="平台SKU" />
          <el-table-column prop="app_food_code" label="本系统编码" />
          <el-table-column prop="product_name" label="商品" />
          <el-table-column prop="option_signature" label="规格签名" width="100" />
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="回调日志" name="logs">
        <el-button @click="loadLogs">刷新</el-button>
        <el-table :data="logs" style="margin-top:12px" size="small">
          <el-table-column prop="created_at" label="时间" width="170" />
          <el-table-column prop="platform" label="平台" width="80" />
          <el-table-column prop="event_type" label="事件" width="120" />
          <el-table-column prop="platform_order_id" label="平台单号" min-width="140" />
        </el-table>
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="showMapDialog" title="新增商品映射" width="480px">
      <el-form label-width="100px">
        <el-form-item label="平台">
          <el-select v-model="mapForm.platform">
            <el-option label="美团" value="MEITUAN" />
            <el-option label="饿了么" value="ELEME" />
          </el-select>
        </el-form-item>
        <el-form-item label="平台SKU">
          <el-input v-model="mapForm.platform_sku_id" />
        </el-form-item>
        <el-form-item label="本系统商品ID">
          <el-input v-model.number="mapForm.product_id" />
        </el-form-item>
        <el-form-item label="app_food_code">
          <el-input v-model="mapForm.app_food_code" placeholder="默认=product_no" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showMapDialog = false">取消</el-button>
        <el-button type="primary" @click="saveMap">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api'

const tab = ref('orders')
const platformFilter = ref('')
const orders = ref<any[]>([])
const refunds = ref<any[]>([])
const bindings = ref<any[]>([])
const maps = ref<any[]>([])
const logs = ref<any[]>([])
const showMapDialog = ref(false)

const syncPlatform = ref('MEITUAN')

const bindingForm = reactive({
  platform: 'MEITUAN',
  platform_shop_id: '',
  platform_shop_name: '',
  access_token: '',
  refresh_token: ''
})

const mapForm = reactive({
  platform: 'MEITUAN',
  platform_sku_id: '',
  product_id: 0,
  app_food_code: ''
})

async function loadOrders() {
  const res = await api.get('/delivery/orders', { params: { platform: platformFilter.value || undefined } })
  orders.value = res.data.items
}

async function loadRefunds() {
  const res = await api.get('/delivery/refunds')
  refunds.value = res.data.items
}

async function loadBindings() {
  const res = await api.get('/delivery/store-bindings')
  bindings.value = res.data
}

async function loadMaps() {
  const res = await api.get('/delivery/product-maps')
  maps.value = res.data
}

async function loadLogs() {
  const res = await api.get('/delivery/callback-logs')
  logs.value = res.data.items
}

async function saveBinding() {
  await api.post('/delivery/store-bindings', bindingForm)
  ElMessage.success('保存成功')
  loadBindings()
}

async function saveMap() {
  await api.post('/delivery/product-maps', mapForm)
  ElMessage.success('保存成功')
  showMapDialog.value = false
  loadMaps()
}

async function handleRefund(id: number, approve: boolean) {
  await api.post(`/delivery/refunds/${id}/action`, { approve, reason: approve ? undefined : '商家拒绝' })
  ElMessage.success(approve ? '已同意退款' : '已拒绝退款')
  loadRefunds()
  loadOrders()
}

async function syncMenu() {
  const res = await api.post('/delivery/sync/menu', null, { params: { platform: syncPlatform.value } })
  ElMessage.success(`同步 ${res.data.synced}/${res.data.total} 个商品`)
}

async function syncPrices() {
  const res = await api.post('/delivery/sync/prices', null, { params: { platform: syncPlatform.value } })
  ElMessage.success(`更新 ${res.data.updated} 个价格`)
}

async function syncStock() {
  const res = await api.post('/delivery/sync/stock', null, { params: { platform: syncPlatform.value } })
  ElMessage.success(`更新 ${res.data.updated} 个库存`)
}

onMounted(() => {
  loadOrders()
  loadRefunds()
  loadBindings()
  loadMaps()
  loadLogs()
})
</script>
