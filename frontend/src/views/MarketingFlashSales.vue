<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2>限量免费抢购</h2>
        <p class="sub">
          射洪霸王面等限量免费活动：按门店配置名额与本店抢购餐品；顾客抢到资格后在有效期内到店核销，
          核销生成 0 元免单订单（不计入营业收入），核销成功后才触发语音播报。
        </p>
      </div>
      <div class="header-actions">
        <el-button @click="goCampaigns">营销活动</el-button>
        <el-button @click="load">刷新</el-button>
      </div>
    </div>

    <div class="filter-bar">
      <el-select
        v-if="auth.isSuperAdmin"
        v-model="filterStoreId"
        clearable
        placeholder="门店（空=全部）"
        style="width: 200px"
        @change="onStoreChange"
      >
        <el-option v-for="s in storeOptions" :key="s.id" :label="s.name" :value="s.id" />
      </el-select>
      <el-tag v-else-if="boundStoreName" type="info">门店：{{ boundStoreName }}</el-tag>
      <el-select
        v-model="campaignId"
        filterable
        clearable
        placeholder="选择抢购活动"
        style="min-width: 260px"
        @change="loadDetail"
      >
        <el-option
          v-for="c in campaigns"
          :key="c.id"
          :label="`${c.name}（${c.code}）`"
          :value="c.id"
        />
      </el-select>
      <el-tag v-if="detail?.is_hq" type="warning">总部活动（默认全部门店参与）</el-tag>
      <el-tag v-else-if="detail" type="success">门店活动</el-tag>
    </div>

    <el-empty v-if="!campaignId" description="请选择要配置的抢购活动；活动需先在「营销活动」创建" />

    <template v-else>
      <el-alert
        v-if="!detail?.config"
        type="warning"
        show-icon
        :closable="false"
        class="tip"
        title="尚未配置抢购"
        description="选择抢购餐品并保存后，顾客才会在小程序看到本活动。总部活动需为每个参与门店分别选择本店餐品与名额。"
      />

      <el-card class="block">
        <template #header>
          <div class="card-head">
            <span>抢购配置</span>
            <span class="hint">每店名额扣减使用条件更新，不会超卖；名额调整不得低于已抢数量</span>
          </div>
        </template>
        <el-form :model="form" label-width="130px" :disabled="!canManage">
          <el-form-item v-if="!detail?.is_hq" label="抢购餐品" required>
            <el-select
              v-model="form.product_id"
              filterable
              placeholder="选择本店在售餐品"
              style="width: 320px"
            >
              <el-option
                v-for="p in productsFor(activeStoreId)"
                :key="p.id"
                :label="`${p.name}（￥${p.price}）`"
                :value="p.id"
              />
            </el-select>
          </el-form-item>
          <el-form-item v-else label="总部默认餐品">
            <el-select
              v-model="form.product_id"
              filterable
              clearable
              placeholder="可选：任选一餐品作为默认值"
              style="width: 320px"
            >
              <el-option
                v-for="p in allProducts"
                :key="p.id"
                :label="`${p.name}（${storeLabel(p.store_id)}）`"
                :value="p.id"
              />
            </el-select>
            <div class="hint">各门店实际抢购餐品在下方门店名额表中选择（餐品按门店隔离）。</div>
          </el-form-item>
          <el-form-item label="每人限购">
            <el-input-number v-model="form.per_customer_limit" :min="1" :max="20" />
            <span class="unit">份</span>
          </el-form-item>
          <el-form-item label="资格有效时长">
            <el-input-number v-model="form.claim_valid_minutes" :min="1" :max="1440" />
            <span class="unit">分钟（抢到后须在该时长内核销）</span>
          </el-form-item>
          <el-form-item label="核销截止时间">
            <el-date-picker
              v-model="form.verify_deadline_at"
              type="datetime"
              value-format="YYYY-MM-DD HH:mm:ss"
              placeholder="留空=跟随活动结束时间"
              style="width: 240px"
            />
          </el-form-item>
          <el-form-item label="活动说明">
            <el-input
              v-model="form.notice"
              maxlength="200"
              show-word-limit
              placeholder="展示给顾客的提示，如「每日限量，先到先得」"
              style="width: 460px"
            />
          </el-form-item>
        </el-form>

        <el-table :data="storeRows" size="small" stripe>
          <el-table-column label="门店" min-width="140">
            <template #default="{ row }">
              {{ row.store_name }}<span class="hint">（{{ row.store_no }}）</span>
            </template>
          </el-table-column>
          <el-table-column v-if="detail?.is_hq" label="参与" width="90">
            <template #default="{ row }">
              <el-switch v-model="row.included" :disabled="!canManage" />
            </template>
          </el-table-column>
          <el-table-column label="本店抢购餐品" min-width="220">
            <template #default="{ row }">
              <el-select
                v-model="row.product_id"
                filterable
                clearable
                placeholder="选择本店在售餐品"
                :disabled="!canManage"
                @visible-change="(v: boolean) => v && ensureStoreProducts(row.store_id)"
              >
                <el-option
                  v-for="p in productsFor(row.store_id)"
                  :key="p.id"
                  :label="p.name"
                  :value="p.id"
                />
              </el-select>
            </template>
          </el-table-column>
          <el-table-column label="总名额" width="150">
            <template #default="{ row }">
              <el-input-number
                v-model="row.total_quantity"
                :min="row.sold_quantity || 0"
                :max="100000"
                :disabled="!canManage"
                size="small"
              />
            </template>
          </el-table-column>
          <el-table-column prop="sold_quantity" label="已抢" width="80" />
          <el-table-column prop="remaining_quantity" label="剩余" width="80" />
        </el-table>

        <div v-if="detail?.is_hq" class="hint">
          总部活动默认全部门店参与；关闭某门店的「参与」后该店顾客不可见、不可抢。
        </div>
        <div class="actions">
          <el-button v-if="canManage" type="primary" :loading="saving" @click="saveConfig">
            保存配置
          </el-button>
          <el-button v-else disabled>缺少 coupon:manage 权限，仅可查看</el-button>
        </div>
      </el-card>

      <el-card class="block">
        <template #header>
          <div class="card-head">
            <span>核销</span>
            <span class="hint">核销成功后生成 0 元免单订单，不计入营业收入</span>
          </div>
        </template>
        <div class="verify-bar">
          <el-input
            v-model="verifyForm.claim_no"
            placeholder="输入顾客核销码"
            clearable
            style="width: 260px"
            @keyup.enter="doVerify"
          />
          <el-button
            type="primary"
            :loading="verifying"
            :disabled="!canManage || !verifyForm.claim_no.trim()"
            @click="doVerify"
          >核销</el-button>
        </div>
        <el-alert
          v-if="verifyResult"
          :type="verifyResult.already_verified ? 'info' : 'success'"
          show-icon
          :closable="false"
          class="tip"
          :title="verifyResult.already_verified ? '该资格此前已核销' : '核销成功'"
          :description="verifyResultDescription"
        />
      </el-card>

      <el-card class="block">
        <template #header>
          <div class="card-head">
            <span>抢购资格</span>
            <span class="hint">超管可跨门店查看；本店账号仅本店</span>
          </div>
        </template>
        <div class="filter-bar inner">
          <el-select v-model="claimStatus" clearable placeholder="状态" style="width: 130px" @change="loadClaims(1)">
            <el-option v-for="(lab, code) in STATUS_LABELS" :key="code" :label="lab" :value="code" />
          </el-select>
          <el-input
            v-model="claimKeyword"
            clearable
            placeholder="核销码/顾客编号/姓名"
            style="width: 220px"
            @keyup.enter="loadClaims(1)"
          />
          <el-button @click="loadClaims(1)">查询</el-button>
        </div>
        <el-table v-loading="claimsLoading" :data="claims" size="small" stripe>
          <el-table-column prop="claim_no" label="核销码" min-width="200" />
          <el-table-column label="门店" width="110" show-overflow-tooltip>
            <template #default="{ row }">{{ row.store_name || row.store_id }}</template>
          </el-table-column>
          <el-table-column label="顾客" min-width="140" show-overflow-tooltip>
            <template #default="{ row }">
              {{ row.customer_name || '—' }}<span class="hint">（{{ row.customer_no || row.customer_id }}）</span>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="90">
            <template #default="{ row }">
              <el-tag :type="statusTag(row.status)" size="small">{{ row.status_label }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="claimed_at" label="抢购时间" width="160" />
          <el-table-column prop="expires_at" label="失效时间" width="160" />
          <el-table-column label="核销时间" width="160">
            <template #default="{ row }">{{ row.verified_at || '—' }}</template>
          </el-table-column>
          <el-table-column label="操作员" width="110" show-overflow-tooltip>
            <template #default="{ row }">{{ row.verified_by_name || '—' }}</template>
          </el-table-column>
        </el-table>
        <div class="pager">
          <el-pagination
            :current-page="claimPage"
            :page-size="claimPageSize"
            :total="claimTotal"
            :page-sizes="[20, 50, 100]"
            layout="total, sizes, prev, pager, next"
            @current-change="(p: number) => loadClaims(p)"
            @size-change="onClaimPageSizeChange"
          />
        </div>
      </el-card>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api'
import { useAuthStore } from '@/stores/auth'
import { useStoreOptions } from '@/composables/useStoreOptions'

const STATUS_LABELS: Record<string, string> = {
  CLAIMED: '待核销',
  USED: '已核销',
  EXPIRED: '已失效',
  CANCELLED: '已取消',
}

const router = useRouter()
const auth = useAuthStore()
const { storeOptions, loadStores } = useStoreOptions()

const loading = ref(false)
const saving = ref(false)
const verifying = ref(false)
const claimsLoading = ref(false)

const filterStoreId = ref<number | undefined>()
const campaigns = ref<any[]>([])
const campaignId = ref<number | undefined>()
const detail = ref<any>(null)
const storeRows = ref<any[]>([])
const storeProducts = reactive<Record<number, any[]>>({})

const form = reactive({
  product_id: null as number | null,
  per_customer_limit: 1,
  claim_valid_minutes: 30,
  verify_deadline_at: null as string | null,
  notice: '',
})

const verifyForm = reactive({ claim_no: '' })
const verifyResult = ref<any>(null)

const claims = ref<any[]>([])
const claimStatus = ref<string | undefined>()
const claimKeyword = ref('')
const claimPage = ref(1)
const claimPageSize = ref(20)
const claimTotal = ref(0)

const canManage = computed(
  () => auth.isSuperAdmin || auth.hasPermission('coupon:manage'),
)
const activeStoreId = computed<number | null>(() => {
  if (auth.isSuperAdmin) return filterStoreId.value ?? null
  return (auth.user?.store_id as number | undefined) ?? null
})
const boundStoreName = computed(() => {
  const sid = auth.user?.store_id
  return storeOptions.value.find((s: any) => s.id === sid)?.name || ''
})
const allProducts = computed(() =>
  Object.values(storeProducts).flat().filter((p: any, i: number, arr: any[]) =>
    arr.findIndex((x) => x.id === p.id) === i,
  ),
)

function goCampaigns() {
  router.push('/marketing/campaigns')
}

function storeLabel(id: number | null | undefined) {
  if (id == null) return '总部'
  return storeOptions.value.find((s: any) => s.id === id)?.name || `#${id}`
}

function productsFor(storeId: number | null) {
  if (!storeId) return []
  return storeProducts[storeId] || []
}

function statusTag(status: string) {
  if (status === 'USED') return 'success'
  if (status === 'EXPIRED') return 'warning'
  if (status === 'CANCELLED') return 'info'
  return 'primary'
}

const verifyResultDescription = computed(() => {
  const r = verifyResult.value
  if (!r) return ''
  return `订单号 ${r.order_no || '—'}；餐品 ${r.product_name || '—'}；金额 ￥${r.total_amount || '0.00'}；核销时间 ${r.verified_at || '—'}`
})

async function loadStoreProductList(storeId: number) {
  if (storeProducts[storeId]) return
  const all: any[] = []
  let page = 1
  let totalPages = 1
  try {
    do {
      const res: any = await api.get('/products', {
        params: { page, page_size: 200, status: 'ON_SALE', store_id: storeId },
      })
      const data = res.data || {}
      all.push(...(data.items || []))
      totalPages = Math.max(1, Number(data.total_pages || 1))
      page += 1
    } while (page <= totalPages && page <= 20)
  } catch {
    /* 门店餐品加载失败时保留空列表，用户可稍后重试 */
  }
  storeProducts[storeId] = all
}

async function ensureStoreProducts(storeId: number) {
  await loadStoreProductList(storeId)
}

async function load() {
  loading.value = true
  try {
    await loadStores()
    await loadCampaigns()
  } finally {
    loading.value = false
  }
}

async function loadCampaigns() {
  const sid = activeStoreId.value
  try {
    const res: any = await api.get('/marketing/campaigns', {
      params: { page_size: 100, ...(sid ? { store_id: sid } : {}) },
    })
    campaigns.value = res.data?.items || []
  } catch (e: any) {
    ElMessage.error(e?.message || '加载活动失败')
    campaigns.value = []
  }
}

function onStoreChange() {
  campaignId.value = undefined
  detail.value = null
  storeRows.value = []
  verifyResult.value = null
  loadCampaigns()
}

async function loadDetail() {
  verifyResult.value = null
  if (!campaignId.value) {
    detail.value = null
    storeRows.value = []
    return
  }
  try {
    const res: any = await api.get(`/marketing/flash-sales/campaigns/${campaignId.value}`)
    detail.value = res.data
    const cfg = res.data?.config
    form.product_id = cfg?.product_id ?? null
    form.per_customer_limit = cfg?.per_customer_limit ?? 1
    form.claim_valid_minutes = cfg?.claim_valid_minutes ?? 30
    form.verify_deadline_at = cfg?.verify_deadline_at ?? null
    form.notice = cfg?.notice ?? ''
    storeRows.value = (res.data?.stores || []).map((s: any) => ({
      store_id: s.store_id,
      store_no: s.store_no,
      store_name: s.store_name,
      included: s.included,
      product_id: s.product_id,
      product_name: s.product_name,
      total_quantity: s.total_quantity,
      remaining_quantity: s.remaining_quantity,
      sold_quantity: s.sold_quantity,
    }))
    await Promise.all(storeRows.value.map((r) => loadStoreProductList(r.store_id)))
    await loadClaims(1)
  } catch (e: any) {
    ElMessage.error(e?.message || '加载抢购配置失败')
    detail.value = null
    storeRows.value = []
  }
}

async function saveParticipationIfNeeded() {
  if (!detail.value?.is_hq) return
  const excluded = storeRows.value.filter((r) => !r.included).map((r) => r.store_id)
  await api.put(`/marketing/flash-sales/campaigns/${campaignId.value}/participation`, {
    excluded_store_ids: excluded,
  })
}

async function saveConfig() {
  if (!canManage.value) return
  const quotas = storeRows.value
    .filter((r) => r.product_id)
    .map((r) => ({
      store_id: r.store_id,
      product_id: r.product_id,
      total_quantity: Number(r.total_quantity || 0),
    }))
  let productId: number | null = form.product_id
  if (detail.value?.is_hq) {
    if (!productId) productId = quotas[0]?.product_id ?? null
  }
  if (!productId) {
    ElMessage.warning('请选择抢购餐品')
    return
  }
  const unfilled = storeRows.value.filter((r) => r.included && !r.product_id)
  if (detail.value?.is_hq && unfilled.length) {
    await ElMessageBox.confirm(
      `门店「${unfilled.map((r) => r.store_name).join('、')}」尚未选择本店抢购餐品，该店将无法抢购。是否继续？`,
      '提示',
      { type: 'warning' },
    )
  }
  saving.value = true
  try {
    await saveParticipationIfNeeded()
    const res: any = await api.put(`/marketing/flash-sales/campaigns/${campaignId.value}/config`, {
      product_id: productId,
      per_customer_limit: form.per_customer_limit,
      claim_valid_minutes: form.claim_valid_minutes,
      verify_deadline_at: form.verify_deadline_at || null,
      notice: form.notice || null,
      store_quotas: quotas,
    })
    detail.value = res.data
    ElMessage.success('抢购配置已保存')
    await loadDetail()
  } catch (e: any) {
    if (e !== 'cancel' && e !== 'close') ElMessage.error(e?.message || '保存失败')
  } finally {
    saving.value = false
  }
}

async function doVerify() {
  const code = verifyForm.claim_no.trim()
  if (!canManage.value || !code) return
  verifying.value = true
  try {
    const res: any = await api.post('/marketing/flash-sales/claims/verify', {
      claim_no: code,
      store_id: auth.isSuperAdmin ? filterStoreId.value : undefined,
    })
    verifyResult.value = res.data
    ElMessage.success(res.data?.already_verified ? '该资格此前已核销' : '核销成功')
    verifyForm.claim_no = ''
    await loadDetail()
  } catch (e: any) {
    ElMessage.error(e?.message || '核销失败')
  } finally {
    verifying.value = false
  }
}

async function loadClaims(page = 1) {
  if (!campaignId.value) return
  claimPage.value = page
  claimsLoading.value = true
  try {
    const res: any = await api.get('/marketing/flash-sales/claims', {
      params: {
        campaign_id: campaignId.value,
        page,
        page_size: claimPageSize.value,
        ...(claimStatus.value ? { status: claimStatus.value } : {}),
        ...(claimKeyword.value.trim() ? { keyword: claimKeyword.value.trim() } : {}),
        ...(auth.isSuperAdmin && filterStoreId.value ? { store_id: filterStoreId.value } : {}),
      },
    })
    claims.value = res.data?.items || []
    claimTotal.value = Number(res.data?.total || 0)
  } catch (e: any) {
    ElMessage.error(e?.message || '加载资格列表失败')
    claims.value = []
    claimTotal.value = 0
  } finally {
    claimsLoading.value = false
  }
}

function onClaimPageSizeChange(size: number) {
  claimPageSize.value = size
  loadClaims(1)
}

onMounted(async () => {
  await load()
})
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
.sub { margin: 0; font-size: 13px; color: #888; line-height: 1.5; max-width: 720px; }
.header-actions { display: flex; gap: 8px; flex-wrap: wrap; flex-shrink: 0; }
.tip { margin-bottom: 10px; }
.filter-bar {
  margin-bottom: 12px;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}
.filter-bar.inner { margin-bottom: 8px; }
.block { margin-bottom: 12px; }
.card-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 12px;
  font-weight: 600;
}
.card-head .hint { font-weight: 400; }
.hint { margin-top: 4px; font-size: 12px; color: #888; line-height: 1.4; }
.unit { margin-left: 8px; font-size: 12px; color: #888; }
.actions { margin-top: 12px; }
.verify-bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.pager { margin-top: 10px; display: flex; justify-content: flex-end; }
</style>
