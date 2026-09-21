<template>
  <div v-loading="loading" class="wallet-page">
    <div class="page-header">
      <div>
        <h2>会员储值</h2>
        <p class="hint">独立充值档位：实付入本金、赠送金优先核销；余额支付不重复记营收。</p>
      </div>
      <div class="header-actions">
        <el-button @click="reload">刷新</el-button>
        <el-button v-if="canManage" type="primary" @click="openCreate">新增档位</el-button>
      </div>
    </div>

    <el-tabs v-model="tab">
      <el-tab-pane label="充值档位" name="packages">
        <el-table :data="packages" border>
          <el-table-column prop="name" label="档位" min-width="140" />
          <el-table-column prop="pay_amount" label="实付" width="100" />
          <el-table-column prop="gift_amount" label="赠送" width="100" />
          <el-table-column prop="credit_total" label="到账合计" width="110" />
          <el-table-column label="范围" width="90">
            <template #default="{ row }">{{ row.store_id ? `门店#${row.store_id}` : '总部' }}</template>
          </el-table-column>
          <el-table-column prop="sort_order" label="排序" width="80" />
          <el-table-column label="状态" width="90">
            <template #default="{ row }">
              <el-tag :type="row.status === 1 ? 'success' : 'info'" size="small">
                {{ row.status === 1 ? '启用' : '停用' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column v-if="canManage" label="操作" width="160" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
              <el-button link :type="row.status === 1 ? 'warning' : 'success'" @click="toggleStatus(row)">
                {{ row.status === 1 ? '停用' : '启用' }}
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="流水" name="ledger">
        <div class="filters">
          <el-input v-model="ledgerQ.customer_id" placeholder="顾客ID" clearable style="width:140px" />
          <el-select v-model="ledgerQ.entry_type" clearable placeholder="类型" style="width:140px">
            <el-option label="充值" value="TOPUP" />
            <el-option label="消费" value="PAY" />
            <el-option label="退款回补" value="REFUND_RESTORE" />
            <el-option label="调账" value="ADJUST" />
          </el-select>
          <el-button type="primary" @click="loadLedger(1)">查询</el-button>
        </div>
        <el-table :data="ledger.items" border style="margin-top:12px">
          <el-table-column prop="id" label="ID" width="80" />
          <el-table-column prop="customer_id" label="顾客" width="90" />
          <el-table-column prop="entry_type" label="类型" width="120" />
          <el-table-column prop="delta_principal" label="本金变动" width="110" />
          <el-table-column prop="delta_gift" label="赠送变动" width="110" />
          <el-table-column prop="principal_after" label="本金余额" width="110" />
          <el-table-column prop="gift_after" label="赠送余额" width="110" />
          <el-table-column prop="order_id" label="订单" width="90" />
          <el-table-column prop="recharge_order_id" label="充值单" width="90" />
          <el-table-column prop="remark" label="备注" min-width="160" show-overflow-tooltip />
          <el-table-column prop="created_at" label="时间" min-width="170" />
        </el-table>
        <div class="pager">
          <el-pagination
            background
            layout="total, prev, pager, next"
            :total="ledger.total"
            :page-size="ledger.page_size"
            :current-page="ledger.page"
            @current-change="loadLedger"
          />
        </div>
      </el-tab-pane>

      <el-tab-pane v-if="canManage" label="人工调账" name="adjust">
        <el-form :model="adjust" label-width="100px" style="max-width:480px">
          <el-form-item label="顾客ID" required>
            <el-input-number v-model="adjust.customer_id" :min="1" controls-position="right" />
          </el-form-item>
          <el-form-item label="本金变动">
            <el-input-number v-model="adjust.delta_principal" :step="1" :precision="2" controls-position="right" />
          </el-form-item>
          <el-form-item label="赠送变动">
            <el-input-number v-model="adjust.delta_gift" :step="1" :precision="2" controls-position="right" />
          </el-form-item>
          <el-form-item label="备注">
            <el-input v-model="adjust.remark" maxlength="255" />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="adjusting" @click="submitAdjust">提交调账</el-button>
          </el-form-item>
        </el-form>
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="dlgVisible" :title="editing?.id ? '编辑档位' : '新增档位'" width="480px">
      <el-form :model="editing" label-width="90px">
        <el-form-item label="名称" required>
          <el-input v-model="editing.name" maxlength="100" />
        </el-form-item>
        <el-form-item label="实付金额" required>
          <el-input-number v-model="editing.pay_amount" :min="0.01" :step="10" :precision="2" />
        </el-form-item>
        <el-form-item label="赠送金额">
          <el-input-number v-model="editing.gift_amount" :min="0" :step="1" :precision="2" />
        </el-form-item>
        <el-form-item label="排序">
          <el-input-number v-model="editing.sort_order" :min="0" :step="1" />
        </el-form-item>
        <el-form-item label="状态">
          <el-switch v-model="editing.statusOn" active-text="启用" inactive-text="停用" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dlgVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="savePackage">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const canManage = computed(() => auth.hasPermission('wallet:manage'))
const loading = ref(false)
const tab = ref('packages')
const packages = ref<any[]>([])
const ledger = reactive({ items: [] as any[], total: 0, page: 1, page_size: 20 })
const ledgerQ = reactive({ customer_id: '', entry_type: '' })
const dlgVisible = ref(false)
const saving = ref(false)
const adjusting = ref(false)
const editing = reactive({
  id: 0,
  name: '',
  pay_amount: 50,
  gift_amount: 5,
  sort_order: 0,
  statusOn: true,
})
const adjust = reactive({
  customer_id: undefined as number | undefined,
  delta_principal: 0,
  delta_gift: 0,
  remark: '',
})

async function loadPackages() {
  const res: any = await api.get('/wallet/packages', { params: { include_disabled: true } })
  const rows = res?.data
  packages.value = Array.isArray(rows) ? rows : []
}

async function loadLedger(page = 1) {
  const params: Record<string, any> = { page, page_size: ledger.page_size }
  if (ledgerQ.customer_id) params.customer_id = Number(ledgerQ.customer_id)
  if (ledgerQ.entry_type) params.entry_type = ledgerQ.entry_type
  const res: any = await api.get('/wallet/ledger', { params })
  const data = res?.data || {}
  ledger.items = data.items || []
  ledger.total = data.total || 0
  ledger.page = data.page || page
  ledger.page_size = data.page_size || 20
}

async function reload() {
  loading.value = true
  try {
    await loadPackages()
    if (tab.value === 'ledger') await loadLedger(ledger.page)
  } catch (e: any) {
    ElMessage.error(e?.message || '加载失败')
  } finally {
    loading.value = false
  }
}

watch(tab, (v) => {
  if (v === 'ledger') loadLedger(1)
})

function openCreate() {
  Object.assign(editing, { id: 0, name: '充50得55', pay_amount: 50, gift_amount: 5, sort_order: 0, statusOn: true })
  dlgVisible.value = true
}

function openEdit(row: any) {
  Object.assign(editing, {
    id: row.id,
    name: row.name,
    pay_amount: Number(row.pay_amount),
    gift_amount: Number(row.gift_amount),
    sort_order: Number(row.sort_order || 0),
    statusOn: Number(row.status) === 1,
  })
  dlgVisible.value = true
}

async function savePackage() {
  if (!editing.name.trim()) {
    ElMessage.warning('请填写档位名称')
    return
  }
  saving.value = true
  try {
    const body = {
      name: editing.name.trim(),
      pay_amount: editing.pay_amount,
      gift_amount: editing.gift_amount,
      sort_order: editing.sort_order,
      status: editing.statusOn ? 1 : 0,
    }
    if (editing.id) {
      await api.put(`/wallet/packages/${editing.id}`, body)
    } else {
      await api.post('/wallet/packages', body)
    }
    ElMessage.success('已保存')
    dlgVisible.value = false
    await loadPackages()
  } catch (e: any) {
    ElMessage.error(e?.message || '保存失败')
  } finally {
    saving.value = false
  }
}

async function toggleStatus(row: any) {
  const next = Number(row.status) === 1 ? 0 : 1
  await api.put(`/wallet/packages/${row.id}`, { status: next })
  ElMessage.success(next ? '已启用' : '已停用')
  await loadPackages()
}

async function submitAdjust() {
  if (!adjust.customer_id) {
    ElMessage.warning('请填写顾客ID')
    return
  }
  if (!adjust.delta_principal && !adjust.delta_gift) {
    ElMessage.warning('本金/赠送至少填一项')
    return
  }
  await ElMessageBox.confirm('确认提交人工调账？', '调账确认', { type: 'warning' })
  adjusting.value = true
  try {
    await api.post('/wallet/adjust', {
      customer_id: adjust.customer_id,
      delta_principal: adjust.delta_principal,
      delta_gift: adjust.delta_gift,
      remark: adjust.remark || undefined,
    })
    ElMessage.success('调账成功')
    tab.value = 'ledger'
    await loadLedger(1)
  } catch (e: any) {
    ElMessage.error(e?.message || '调账失败')
  } finally {
    adjusting.value = false
  }
}

onMounted(reload)
</script>

<style scoped>
.wallet-page { padding: 4px 8px 24px; }
.page-header { display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; }
.page-header h2 { margin: 0 0 6px; font-size: 20px; }
.hint { margin: 0; color: #666; font-size: 13px; }
.filters { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.pager { margin-top: 12px; display: flex; justify-content: flex-end; }
</style>
