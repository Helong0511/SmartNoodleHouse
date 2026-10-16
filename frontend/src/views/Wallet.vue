<template>
  <div v-loading="loading" class="wallet-page">
    <div class="page-header">
      <div>
        <h2>会员储值</h2>
        <p class="hint">独立充值档位：实付入本金、赠送金优先核销；余额支付不重复记营收。</p>
      </div>
    </div>

    <el-tabs v-model="tab">
      <el-tab-pane label="流水" name="ledger">
        <div class="toolbar">
          <el-select v-model="ledgerQ.customer_id" filterable remote clearable reserve-keyword
            placeholder="搜索顾客（昵称/手机/编号）" :remote-method="searchLedgerCustomers" :loading="ledgerCustomerSearching"
            style="width: 260px" @change="loadLedger(1)" @clear="loadLedger(1)">
            <el-option v-for="c in ledgerCustomerOptions" :key="c.id" :label="customerOptionLabel(c)" :value="c.id">
              <div class="opt-row">
                <el-avatar :size="28" :src="mediaUrl(c.avatar_url) || undefined">客</el-avatar>
                <div class="opt-meta">
                  <div>{{ c.display_name || c.nickname || '顾客' }}</div>
                  <div class="muted">{{ c.phone || '无手机号' }} · {{ c.customer_no }}</div>
                </div>
              </div>
            </el-option>
          </el-select>
          <el-select v-if="auth.isSuperAdmin" v-model="ledgerQ.store_id" clearable placeholder="门店" style="width:160px"
            @change="loadLedger(1)" @clear="loadLedger(1)">
            <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
          </el-select>
          <el-select v-model="ledgerQ.entry_type" clearable placeholder="类型" style="width:140px" @change="loadLedger(1)"
            @clear="loadLedger(1)">
            <el-option label="充值" value="TOPUP" />
            <el-option label="消费" value="PAY" />
            <el-option label="退款回补" value="REFUND_RESTORE" />
            <el-option label="调账" value="ADJUST" />
          </el-select>
          <el-button type="primary" :loading="ledgerLoading" @click="loadLedger(1)">查询</el-button>
          <el-button :loading="ledgerLoading" @click="loadLedger(ledger.page)">刷新</el-button>
        </div>
        <el-table :data="ledger.items" border style="margin-top:12px">
          <el-table-column prop="id" label="ID" width="80" />
          <el-table-column label="顾客" min-width="220">
            <template #default="{ row }">
              <div class="cust-cell">
                <el-image v-if="row.customer_avatar_url" :src="mediaUrl(row.customer_avatar_url)" fit="cover"
                  class="avatar-thumb" :preview-src-list="[mediaUrl(row.customer_avatar_url)]" preview-teleported :z-index="4000" />
                <span v-else class="no-avatar">无</span>
                <div class="cust-meta">
                  <div class="cust-name">{{ row.customer_name || row.customer_nickname || '—' }}</div>
                  <div class="cust-sub">
                    <span v-if="row.customer_phone">{{ row.customer_phone }}</span>
                    <span v-else class="muted">无手机号</span>
                    <span class="muted"> · #{{ row.customer_id }}</span>
                  </div>
                </div>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="门店" min-width="120">
            <template #default="{ row }">{{ storeName(row.store_id) }}</template>
          </el-table-column>
          <el-table-column label="类型" width="110">
            <template #default="{ row }">
              <el-tag size="small" :type="entryTypeTag(row.entry_type)">
                {{ entryTypeLabel(row.entry_type) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="关联单号" min-width="200">
            <template #default="{ row }">
              <div v-if="row.order_no || row.order_id">
                <div>点餐 {{ row.order_no || `#${row.order_id}` }}</div>
                <div v-if="row.order_id" class="muted">ID {{ row.order_id }}</div>
              </div>
              <div v-else-if="row.recharge_no || row.recharge_order_id">
                <div>充值 {{ row.recharge_no || `#${row.recharge_order_id}` }}</div>
                <div v-if="row.recharge_order_id" class="muted">ID {{ row.recharge_order_id }}</div>
              </div>
              <div v-else-if="row.biz_no">
                <div>{{ row.entry_type === 'ADJUST' ? '调账' : '单号' }} {{ row.biz_no }}</div>
              </div>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
          <el-table-column label="操作人" min-width="140">
            <template #default="{ row }">
              <div>{{ row.operator_name || '—' }}</div>
              <div v-if="row.operator_kind === 'CUSTOMER'" class="muted">顾客自助</div>
              <div v-else-if="row.operator_kind === 'STAFF'" class="muted">员工</div>
            </template>
          </el-table-column>
          <el-table-column prop="delta_principal" label="本金变动" width="110" />
          <el-table-column prop="delta_gift" label="赠送变动" width="110" />
          <el-table-column prop="principal_after" label="本金余额" width="110" />
          <el-table-column prop="gift_after" label="赠送余额" width="110" />
          <el-table-column prop="remark" label="备注" min-width="160" show-overflow-tooltip />
          <el-table-column prop="created_at" label="时间" min-width="170" />
        </el-table>
        <div class="pager">
          <el-pagination background layout="total, prev, pager, next" :total="ledger.total"
            :page-size="ledger.page_size" :current-page="ledger.page" @current-change="loadLedger" />
        </div>
      </el-tab-pane>

      <el-tab-pane v-if="canManage" label="人工调账" name="adjust">
        <el-form :model="adjust" label-width="100px" style="max-width:520px">
          <el-form-item v-if="auth.isSuperAdmin" label="归属门店">
            <el-select v-model="adjust.store_id" clearable placeholder="可选，记入流水" style="width:100%">
              <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
            </el-select>
          </el-form-item>
          <el-form-item label="顾客" required>
            <el-select v-model="adjust.customer_id" filterable remote clearable reserve-keyword
              placeholder="输入昵称 / 手机 / 编号搜索" :remote-method="searchAdjustCustomers" :loading="adjustCustomerSearching"
              style="width: 100%" @change="onAdjustCustomerChange">
              <el-option v-for="c in adjustCustomerOptions" :key="c.id" :label="customerOptionLabel(c)" :value="c.id">
                <div class="opt-row">
                  <el-avatar :size="28" :src="mediaUrl(c.avatar_url) || undefined">客</el-avatar>
                  <div class="opt-meta">
                    <div>{{ c.display_name || c.nickname || '顾客' }}</div>
                    <div class="muted">
                      {{ c.phone || '无手机号' }} · 余额 ¥{{ c.balance_total || '0.00' }}
                    </div>
                  </div>
                </div>
              </el-option>
            </el-select>
          </el-form-item>
          <el-form-item v-if="selectedAdjustCustomer" label="当前余额">
            <div class="selected-cust">
              <el-avatar :size="44" :src="mediaUrl(selectedAdjustCustomer.avatar_url) || undefined">客</el-avatar>
              <div class="selected-meta">
                <div class="cust-name">
                  {{ selectedAdjustCustomer.display_name || selectedAdjustCustomer.nickname || '顾客' }}
                  <span class="muted"> · #{{ selectedAdjustCustomer.id }}</span>
                </div>
                <div class="cust-sub">
                  {{ selectedAdjustCustomer.phone || '无手机号' }}
                  · {{ selectedAdjustCustomer.customer_no }}
                </div>
                <div class="balance-line">
                  本金 ¥{{ selectedAdjustCustomer.balance_principal || '0.00' }}
                  · 赠送 ¥{{ selectedAdjustCustomer.balance_gift || '0.00' }}
                  · 合计 <strong>¥{{ selectedAdjustCustomer.balance_total || '0.00' }}</strong>
                </div>
              </div>
            </div>
          </el-form-item>
          <el-form-item label="本金变动">
            <el-input-number v-model="adjust.delta_principal" :step="1" :precision="2" controls-position="right"
              style="width: 100%" />
          </el-form-item>
          <el-form-item label="赠送变动">
            <el-input-number v-model="adjust.delta_gift" :step="1" :precision="2" controls-position="right"
              style="width: 100%" />
          </el-form-item>
          <el-form-item label="备注">
            <el-input v-model="adjust.remark" maxlength="255" placeholder="调账原因，建议填写" />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="adjusting" @click="submitAdjust">提交调账</el-button>
          </el-form-item>
        </el-form>
      </el-tab-pane>

      <el-tab-pane label="充值档位" name="packages">
        <div class="toolbar">
          <el-select v-if="auth.isSuperAdmin" v-model="pkgFilterStoreId" clearable placeholder="全部门店范围"
            style="width: 200px" @change="loadPackages">
            <el-option label="仅总部" :value="0" />
            <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
          </el-select>
          <el-button @click="reloadPackages">刷新</el-button>
          <el-button v-if="canManage" type="primary" @click="openCreate">新增档位</el-button>
        </div>
        <el-table :data="packages" border style="margin-top:12px">
          <el-table-column prop="name" label="档位" min-width="140" />
          <el-table-column prop="pay_amount" label="实付" width="100" />
          <el-table-column prop="gift_amount" label="赠送" width="100" />
          <el-table-column prop="credit_total" label="到账合计" width="110" />
          <el-table-column label="归属门店" min-width="140">
            <template #default="{ row }">
              <el-tag size="small" :type="!row.store_id ? 'info' : 'success'">
                {{ scopeLabel(row) }}
              </el-tag>
            </template>
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
              <template v-if="canEditPackage(row)">
                <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
                <el-button link :type="row.status === 1 ? 'warning' : 'success'" @click="toggleStatus(row)">
                  {{ row.status === 1 ? '停用' : '启用' }}
                </el-button>
              </template>
              <span v-else class="muted">总部只读</span>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="dlgVisible" :title="editing.id ? '编辑档位' : '新增档位'" width="480px" destroy-on-close>
      <el-form :model="editing" label-width="90px">
        <el-form-item v-if="!editing.id && auth.isSuperAdmin" label="归属" required>
          <el-radio-group v-model="editing.scope">
            <el-radio value="HQ">总部（全店可用）</el-radio>
            <el-radio value="STORE">指定门店</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="!editing.id && auth.isSuperAdmin && editing.scope === 'STORE'" label="门店" required>
          <el-select v-model="editing.store_id" placeholder="选择门店" style="width: 100%">
            <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="editing.id" label="归属">
          <el-tag size="small" :type="editing.scope === 'HQ' ? 'info' : 'success'">
            {{ editing.scope === 'HQ' ? '总部（全店可用）' : storeName(editing.store_id) }}
          </el-tag>
        </el-form-item>
        <el-form-item v-if="!editing.id && !auth.isSuperAdmin" label="归属">
          <el-tag size="small" type="success">本店</el-tag>
        </el-form-item>
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
import { useStoreOptions } from '@/composables/useStoreOptions'
import { useAuthStore } from '@/stores/auth'
import { mediaUrl } from '@/utils/media'

const ENTRY_TYPE_LABELS: Record<string, string> = {
  TOPUP: '充值',
  PAY: '消费',
  REFUND_RESTORE: '退款回补',
  ADJUST: '调账',
}

function entryTypeLabel(t?: string | null) {
  const key = String(t || '').toUpperCase()
  return ENTRY_TYPE_LABELS[key] || '—'
}

function entryTypeTag(t?: string | null): '' | 'success' | 'warning' | 'info' | 'danger' {
  const key = String(t || '').toUpperCase()
  if (key === 'TOPUP') return 'success'
  if (key === 'PAY') return 'warning'
  if (key === 'REFUND_RESTORE') return 'info'
  if (key === 'ADJUST') return 'danger'
  return ''
}

const auth = useAuthStore()
const canManage = computed(() => auth.hasPermission('wallet:manage'))
const loading = ref(false)
const tab = ref('ledger')
const packages = ref<any[]>([])
const { storeOptions: stores, loadStores } = useStoreOptions()
/** 超管筛选：undefined=全部，0=仅总部，>0=指定门店 */
const pkgFilterStoreId = ref<number | undefined>(undefined)
const ledger = reactive({ items: [] as any[], total: 0, page: 1, page_size: 20 })
const ledgerQ = reactive({
  customer_id: undefined as number | undefined,
  entry_type: '',
  store_id: undefined as number | undefined,
})
const ledgerCustomerOptions = ref<any[]>([])
const ledgerCustomerSearching = ref(false)
const ledgerLoading = ref(false)
const dlgVisible = ref(false)
const saving = ref(false)
const adjusting = ref(false)
const adjustCustomerOptions = ref<any[]>([])
const adjustCustomerSearching = ref(false)
const selectedAdjustCustomer = ref<any | null>(null)
const editing = reactive({
  id: 0,
  name: '',
  pay_amount: 50,
  gift_amount: 5,
  sort_order: 0,
  statusOn: true,
  scope: 'HQ' as 'HQ' | 'STORE',
  store_id: undefined as number | undefined,
})
const adjust = reactive({
  customer_id: undefined as number | undefined,
  store_id: undefined as number | undefined,
  delta_principal: 0,
  delta_gift: 0,
  remark: '',
})

function customerOptionLabel(c: any) {
  const name = c?.display_name || c?.nickname || '顾客'
  const phone = c?.phone ? ` ${c.phone}` : ''
  return `${name}${phone}（${c?.customer_no || c?.id}）`
}

async function searchWalletCustomers(keyword: string): Promise<any[]> {
  const kw = (keyword || '').trim()
  if (!kw) return []
  const res: any = await api.get('/wallet/customers/search', { params: { keyword: kw, page_size: 20 } })
  return res?.data?.items || []
}

async function searchLedgerCustomers(q: string) {
  const kw = (q || '').trim()
  if (!kw) {
    ledgerCustomerOptions.value = []
    return
  }
  ledgerCustomerSearching.value = true
  try {
    ledgerCustomerOptions.value = await searchWalletCustomers(kw)
  } catch {
    ledgerCustomerOptions.value = []
  } finally {
    ledgerCustomerSearching.value = false
  }
}

async function searchAdjustCustomers(q: string) {
  const kw = (q || '').trim()
  if (!kw) {
    adjustCustomerOptions.value = []
    return
  }
  adjustCustomerSearching.value = true
  try {
    adjustCustomerOptions.value = await searchWalletCustomers(kw)
  } catch {
    adjustCustomerOptions.value = []
  } finally {
    adjustCustomerSearching.value = false
  }
}

function onAdjustCustomerChange(id: number | undefined) {
  if (!id) {
    selectedAdjustCustomer.value = null
    return
  }
  selectedAdjustCustomer.value =
    adjustCustomerOptions.value.find((c) => c.id === id) || selectedAdjustCustomer.value
}

function storeName(storeId: number | null | undefined) {
  if (!storeId) return '—'
  const s = stores.value.find((x) => x.id === storeId)
  return s?.name || `门店#${storeId}`
}

function scopeLabel(row: any) {
  if (!row?.store_id || row.store_scope === 'HQ') return '总部'
  return row.store_name || storeName(row.store_id)
}

/** 门店账号不可改总部档；超管可改全部 */
function canEditPackage(row: any) {
  if (!canManage.value) return false
  if (auth.isSuperAdmin) return true
  return !!(row?.store_id)
}

async function loadPackages() {
  const params: Record<string, any> = { include_disabled: true }
  if (auth.isSuperAdmin) {
    if (pkgFilterStoreId.value === 0) {
      // 仅总部：后端无单独参数，前端过滤
    } else if (pkgFilterStoreId.value) {
      params.store_id = pkgFilterStoreId.value
    }
  }
  const res: any = await api.get('/wallet/packages', { params })
  let rows = Array.isArray(res?.data) ? res.data : []
  if (auth.isSuperAdmin && pkgFilterStoreId.value === 0) {
    rows = rows.filter((r: any) => !r.store_id)
  } else if (auth.isSuperAdmin && pkgFilterStoreId.value) {
    // 后端返回总部+该店，列表筛成「仅该店」或保留总部+该店？优惠券习惯是总部+门店都看。
    // 选具体门店时展示：总部 + 该门店
  }
  packages.value = rows
}

async function loadLedger(page = 1) {
  const params: Record<string, any> = { page, page_size: ledger.page_size }
  if (ledgerQ.customer_id) params.customer_id = Number(ledgerQ.customer_id)
  if (ledgerQ.entry_type) params.entry_type = ledgerQ.entry_type
  if (auth.isSuperAdmin && ledgerQ.store_id) params.store_id = ledgerQ.store_id
  ledgerLoading.value = true
  try {
    const res: any = await api.get('/wallet/ledger', { params })
    const data = res?.data || {}
    ledger.items = data.items || []
    ledger.total = Number(data.total || 0)
    ledger.page = Number(data.page || page)
    ledger.page_size = Number(data.page_size || ledger.page_size || 20)
  } catch (e: any) {
    ElMessage.error(e?.message || '流水加载失败')
  } finally {
    ledgerLoading.value = false
  }
}

async function reloadPackages() {
  loading.value = true
  try {
    await loadPackages()
  } catch (e: any) {
    ElMessage.error(e?.message || '加载失败')
  } finally {
    loading.value = false
  }
}

async function reload() {
  loading.value = true
  try {
    await loadStores()
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
  Object.assign(editing, {
    id: 0,
    name: '充50得55',
    pay_amount: 50,
    gift_amount: 5,
    sort_order: 0,
    statusOn: true,
    scope: auth.isSuperAdmin ? 'HQ' : 'STORE',
    store_id: auth.isSuperAdmin ? stores.value[0]?.id : auth.user?.store_id || undefined,
  })
  dlgVisible.value = true
}

function openEdit(row: any) {
  if (!canEditPackage(row)) {
    ElMessage.warning('无权修改总部档位')
    return
  }
  Object.assign(editing, {
    id: row.id,
    name: row.name,
    pay_amount: Number(row.pay_amount),
    gift_amount: Number(row.gift_amount),
    sort_order: Number(row.sort_order || 0),
    statusOn: Number(row.status) === 1,
    scope: row.store_id ? 'STORE' : 'HQ',
    store_id: row.store_id || undefined,
  })
  dlgVisible.value = true
}

async function savePackage() {
  if (!editing.name.trim()) {
    ElMessage.warning('请填写档位名称')
    return
  }
  if (!editing.id && auth.isSuperAdmin && editing.scope === 'STORE' && !editing.store_id) {
    ElMessage.warning('请选择门店')
    return
  }
  saving.value = true
  try {
    const body: Record<string, any> = {
      name: editing.name.trim(),
      pay_amount: editing.pay_amount,
      gift_amount: editing.gift_amount,
      sort_order: editing.sort_order,
      status: editing.statusOn ? 1 : 0,
    }
    if (editing.id) {
      await api.put(`/wallet/packages/${editing.id}`, body)
    } else {
      if (auth.isSuperAdmin) {
        body.store_id = editing.scope === 'STORE' ? editing.store_id : null
      }
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
  if (!canEditPackage(row)) {
    ElMessage.warning('无权修改总部档位')
    return
  }
  const next = Number(row.status) === 1 ? 0 : 1
  await api.put(`/wallet/packages/${row.id}`, { status: next })
  ElMessage.success(next ? '已启用' : '已停用')
  await loadPackages()
}

async function submitAdjust() {
  if (!adjust.customer_id) {
    ElMessage.warning('请先搜索并选择顾客')
    return
  }
  if (!adjust.delta_principal && !adjust.delta_gift) {
    ElMessage.warning('本金/赠送至少填一项')
    return
  }
  const name =
    selectedAdjustCustomer.value?.display_name ||
    selectedAdjustCustomer.value?.nickname ||
    `#${adjust.customer_id}`
  await ElMessageBox.confirm(
    `确认对「${name}」调账？本金 ${adjust.delta_principal}，赠送 ${adjust.delta_gift}`,
    '调账确认',
    { type: 'warning' },
  )
  adjusting.value = true
  try {
    const res: any = await api.post('/wallet/adjust', {
      customer_id: adjust.customer_id,
      delta_principal: adjust.delta_principal,
      delta_gift: adjust.delta_gift,
      remark: adjust.remark || undefined,
      store_id: auth.isSuperAdmin ? adjust.store_id || undefined : undefined,
    })
    const bizNo = res?.data?.biz_no || res?.biz_no
    ElMessage.success(bizNo ? `调账成功，单号 ${bizNo}` : '调账成功')
    adjust.delta_principal = 0
    adjust.delta_gift = 0
    adjust.remark = ''
    // 刷新已选顾客余额展示
    if (selectedAdjustCustomer.value?.id) {
      const refreshed = await searchWalletCustomers(
        selectedAdjustCustomer.value.phone ||
        selectedAdjustCustomer.value.customer_no ||
        String(selectedAdjustCustomer.value.id),
      )
      const hit = refreshed.find((c) => c.id === selectedAdjustCustomer.value.id)
      if (hit) {
        selectedAdjustCustomer.value = hit
        adjustCustomerOptions.value = [hit]
      }
    }
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
.wallet-page {
  padding: 4px 8px 24px;
}

.page-header {
  margin-bottom: 8px;
}

.page-header h2 {
  margin: 0 0 6px;
  font-size: 20px;
}

.hint {
  margin: 0;
  color: #9bb8d8;
  font-size: 13px;
}

.toolbar {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  align-items: center;
}

.pager {
  margin-top: 12px;
  display: flex;
  justify-content: flex-end;
}

.muted {
  color: #6a8fb0;
  font-size: 13px;
}

.cust-cell {
  display: flex;
  align-items: center;
  gap: 10px;
}

.avatar-thumb {
  width: 40px;
  height: 40px;
  border-radius: 50%;
  flex-shrink: 0;
}

.no-avatar {
  width: 40px;
  height: 40px;
  border-radius: 50%;
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: rgba(120, 200, 255, 0.12);
  color: #6a8fb0;
  font-size: 12px;
}

.cust-meta {
  min-width: 0;
  line-height: 1.35;
}

.cust-name {
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cust-sub {
  font-size: 12px;
  color: #9bb8d8;
  margin-top: 2px;
}

.opt-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.opt-meta {
  line-height: 1.3;
}

.selected-cust {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  width: 100%;
  padding: 10px 12px;
  background: rgba(16, 34, 66, 0.7);
  border-radius: 8px;
}

.selected-meta {
  min-width: 0;
  line-height: 1.4;
}

.balance-line {
  margin-top: 6px;
  font-size: 13px;
  color: #333;
}
</style>
