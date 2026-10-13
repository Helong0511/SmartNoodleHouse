<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2>营销活动</h2>
        <p class="sub">绑定分群 + 券模板后发布发券；可选推荐加权商品。状态流：DRAFT → 发布 → RUNNING / PAUSED → 结束。</p>
      </div>
      <div class="header-actions">
        <el-button @click="goSegments">客户分群</el-button>
        <el-button @click="goReport">营销报表</el-button>
        <el-button @click="goCoupons">优惠券模板</el-button>
        <el-button @click="load">刷新</el-button>
        <el-button type="primary" @click="openEdit()">新建活动</el-button>
      </div>
    </div>

    <el-alert
      type="info"
      show-icon
      :closable="false"
      class="tip"
      title="店长怎么用"
      description="① 客户分群估人/物化 → ② 优惠券备模板 → ③ 选活动类型（或点下方快捷模板）→ ④ 选分群与券 → ⑤ 可选推荐加权商品 → ⑥ 发布 → ⑦ 营销报表看 ROI。推荐加权仅 RUNNING 期间约 +25%。活动发券与会员折：FIXED 可叠加、PERCENT 取优互斥；与「社会贡献」身份折：不与券同享，与会员折结算时取更优（折扣更低者）。类型 SOCIAL=爱心日发券，不是身份当场折扣。"
    />

    <div class="preset-bar">
      <span class="preset-label">快捷模板</span>
      <el-button
        v-for="p in CAMPAIGN_PRESETS"
        :key="p.codePrefix"
        size="small"
        plain
        @click="openEditFromPreset(p)"
      >{{ p.label }}</el-button>
    </div>

    <div class="filter-bar">
      <el-select
        v-if="auth.isSuperAdmin"
        v-model="filterStoreId"
        clearable
        placeholder="门店（空=全部/含总部）"
        style="width: 200px"
        @change="load"
      >
        <el-option
          v-for="s in storeOptions"
          :key="s.id"
          :label="s.name"
          :value="s.id"
        />
      </el-select>
      <el-tag v-else-if="boundStoreName" type="info">门店：{{ boundStoreName }}</el-tag>
      <el-select v-model="typeFilter" clearable placeholder="按类型筛选" style="width: 200px">
        <el-option v-for="t in CAMPAIGN_TYPES" :key="t.value" :label="t.label" :value="t.value" />
      </el-select>
      <el-button link type="primary" @click="goStoreBiz">门店营销开关</el-button>
    </div>

    <el-alert
      v-if="marketingEnabled === false"
      type="error"
      show-icon
      :closable="false"
      class="tip"
      title="当前门店已关闭「营销活动」"
      description="不可新建/发布。请到 门店 → 门店信息 → 业务参数 → 会员与优惠 开启「营销活动」。关闭后门店推荐加权也不会生效。"
    />
    <el-alert
      v-else-if="marketingEnabled === true && filterStoreId"
      type="success"
      show-icon
      :closable="false"
      class="tip"
      title="本店营销活动已开启"
      description="可按门店建活动；总部活动（门店列为空）对开启营销的门店仍可共享推荐加权/发券任务。"
    />

    <el-empty v-if="!loading && !filteredItems.length" description="暂无活动；点「新建活动」或上方快捷模板开始" />
    <el-table v-else :data="filteredItems" v-loading="loading" stripe>
      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column label="门店" width="120" show-overflow-tooltip>
        <template #default="{ row }">{{ storeLabel(row.store_id) }}</template>
      </el-table-column>
      <el-table-column prop="code" label="编码" width="140" />
      <el-table-column prop="name" label="名称" min-width="140" />
      <el-table-column label="类型" width="130">
        <template #default="{ row }">{{ typeLabel(row.campaign_type) }}</template>
      </el-table-column>
      <el-table-column prop="status" label="状态" width="110" />
      <el-table-column label="分群" width="140" show-overflow-tooltip>
        <template #default="{ row }">{{ segmentLabel(row.segment_id) }}</template>
      </el-table-column>
      <el-table-column label="券模板" min-width="140" show-overflow-tooltip>
        <template #default="{ row }">{{ couponLabel(row.coupon_template_id) }}</template>
      </el-table-column>
      <el-table-column label="推荐加权" min-width="160" show-overflow-tooltip>
        <template #default="{ row }">{{ boostProductsLabel(row.boost_product_ids) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="320" fixed="right">
        <template #default="{ row }">
          <el-button v-if="row.status === 'DRAFT'" size="small" @click="openEdit(row)">编辑</el-button>
          <el-button
            v-if="row.status === 'DRAFT' || row.status === 'SCHEDULED'"
            size="small"
            type="primary"
            @click="publish(row)"
          >发布</el-button>
          <el-button
            v-if="row.status === 'RUNNING'"
            size="small"
            @click="setStatus(row, 'PAUSED')"
          >暂停</el-button>
          <el-button
            v-if="row.status === 'PAUSED'"
            size="small"
            type="success"
            @click="setStatus(row, 'RUNNING')"
          >恢复</el-button>
          <el-button size="small" @click="showTasks(row)">任务</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dlg" :title="form.id ? '编辑活动' : '新建活动'" width="640px" destroy-on-close>
      <el-alert
        type="info"
        :closable="false"
        show-icon
        style="margin-bottom:12px"
        :title="typeHint(form.campaign_type)"
      />
      <el-form label-width="120px">
        <el-form-item v-if="auth.isSuperAdmin" label="归属门店">
          <el-select v-model="form.store_id" clearable placeholder="总部（全店共享）" style="width:100%" :disabled="!!form.id">
            <el-option
              v-for="s in storeOptions"
              :key="s.id"
              :label="s.name"
              :value="s.id"
            />
          </el-select>
          <div class="hint">空=总部活动（各开启营销的门店可见）；指定门店则仅该店。创建后不可改归属。</div>
        </el-form-item>
        <el-form-item v-else label="归属门店">
          <el-tag type="info">{{ boundStoreName || '本店' }}</el-tag>
        </el-form-item>
        <el-form-item label="编码">
          <el-input v-model="form.code" placeholder="如 festival_spring_2026" />
        </el-form-item>
        <el-form-item label="名称"><el-input v-model="form.name" /></el-form-item>
        <el-form-item label="类型">
          <el-select v-model="form.campaign_type" style="width:100%" filterable>
            <el-option
              v-for="t in CAMPAIGN_TYPES"
              :key="t.value"
              :label="t.label"
              :value="t.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="分群">
          <el-select v-model="form.segment_id" filterable clearable placeholder="选择分群" style="width:100%">
            <el-option
              v-for="s in segments"
              :key="s.id"
              :label="`${s.name} (#${s.id}${s.estimated_count != null ? ' · 约' + s.estimated_count + '人' : ''})`"
              :value="s.id"
            />
          </el-select>
          <div class="hint">建议选用已物化的分群，保证本轮圈人稳定。</div>
        </el-form-item>
        <el-form-item label="券模板">
          <el-select
            v-model="form.coupon_template_id"
            filterable
            clearable
            placeholder="选择启用中的券模板"
            style="width:100%"
          >
            <el-option
              v-for="t in couponTemplates"
              :key="t.id"
              :label="`${t.name} (#${t.id} · ${t.coupon_type || ''})`"
              :value="t.id"
            />
          </el-select>
          <div class="hint">列表来自「优惠券」启用模板；若为空请先去创建并启用。</div>
        </el-form-item>
        <el-form-item label="推荐加权商品">
          <el-select
            v-model="form.boost_product_ids"
            multiple
            filterable
            clearable
            collapse-tags
            collapse-tags-tooltip
            placeholder="搜索并选择商品（可多选）"
            style="width:100%"
            :loading="productsLoading"
          >
            <el-option
              v-for="p in productOptions"
              :key="p.id"
              :label="productOptionLabel(p)"
              :value="p.id"
            />
          </el-select>
          <div class="hint">RUNNING 期间这些 SKU 在推荐列表加权约 +25%；活动结束自动失效。可搜菜名。</div>
        </el-form-item>
        <el-form-item label="备注"><el-input v-model="form.remark" type="textarea" :rows="2" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dlg = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="taskDlg" :title="`任务监控 #${taskCampaignId}`" width="720px">
      <el-alert
        type="info"
        :closable="false"
        show-icon
        style="margin-bottom:12px"
        title="发券任务按幂等键去重。FAILED 可点「重试 FAILED」重置后由 worker 再发。"
      />
      <p v-if="taskStats">
        汇总：
        <span v-for="(v, k) in taskStats" :key="k" style="margin-right: 12px">{{ k }}={{ v }}</span>
      </p>
      <el-button size="small" @click="retryFailed" style="margin-bottom: 8px">重试 FAILED</el-button>
      <el-empty v-if="!tasks.length" description="暂无任务（未发布或尚未入队）" />
      <el-table v-else :data="tasks" size="small" stripe max-height="360">
        <el-table-column prop="id" label="ID" width="70" />
        <el-table-column prop="customer_id" label="顾客" width="90" />
        <el-table-column prop="status" label="状态" width="100" />
        <el-table-column prop="idempotency_key" label="幂等键" min-width="180" />
        <el-table-column prop="last_error" label="错误" min-width="140" />
      </el-table>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api'
import { useAuthStore } from '@/stores/auth'
import { useStoreOptions } from '@/composables/useStoreOptions'

/** 活动类型（与实施方案日历节点对齐；后端存大写字符串） */
const CAMPAIGN_TYPES = [
  { value: 'MEMBER_DAY', label: '会员日 MEMBER_DAY', hint: '老客回访轻券，适合会员日/店庆。' },
  { value: 'FESTIVAL', label: '节日 FESTIVAL', hint: '春节、妇女节、儿童节、重阳、教师节等节日发券。' },
  { value: 'TIME_SLOT', label: '时段 TIME_SLOT', hint: '冬至/下午茶等时段套餐或促销券。' },
  { value: 'SOCIAL', label: '公益营销 SOCIAL', hint: '如每月环卫爱心日发豆浆券；与「社会贡献」身份折扣不同。' },
  { value: 'BIRTHDAY', label: '生日 BIRTHDAY', hint: '生日权益加码发券（可与门店生日参数配合）。' },
  { value: 'NEW_MEMBER', label: '新客 NEW_MEMBER', hint: '新注册/开卡欢迎券包。' },
  { value: 'WAKEUP', label: '唤醒 WAKEUP', hint: '沉睡/流失客召回发券。' },
  { value: 'EXAM', label: '考生 EXAM', hint: '高考/中考加油券，可联动公益身份。' },
  { value: 'RECO_BOOST', label: '推荐加权 RECO_BOOST', hint: '以推荐加权为主；仍可附带发券。' },
] as const

type CampaignPreset = {
  label: string
  campaign_type: string
  codePrefix: string
  name: string
  remark: string
}

const CAMPAIGN_PRESETS: CampaignPreset[] = [
  { label: '春节返乡', campaign_type: 'FESTIVAL', codePrefix: 'festival_spring', name: '春节·回家吃碗家乡面', remark: '返乡券包，锚点豆浆/卤蛋' },
  { label: '妇女/母亲节', campaign_type: 'FESTIVAL', codePrefix: 'festival_mother', name: '妇女节/母亲节·有温度的甜', remark: '女性/家庭向，锚点红糖小汤圆' },
  { label: '儿童节', campaign_type: 'FESTIVAL', codePrefix: 'festival_children', name: '儿童节·小份快乐', remark: '儿童小份抄手等 SKU' },
  { label: '教师节', campaign_type: 'FESTIVAL', codePrefix: 'festival_teacher', name: '教师节·感念师恩', remark: '教师券；身份折扣另走社会贡献 TEACHER' },
  { label: '重阳', campaign_type: 'FESTIVAL', codePrefix: 'festival_chongyang', name: '重阳·长寿面', remark: '长者向粥/汤圆' },
  { label: '冬至时段', campaign_type: 'TIME_SLOT', codePrefix: 'timeslot_dongzhi', name: '冬至·饺/抄手节', remark: '时段套餐促销' },
  { label: '环卫爱心日', campaign_type: 'SOCIAL', codePrefix: 'social_sanitation_day', name: '环卫爱心日·豆浆券', remark: '每月爱心日营销发券，非身份当场折扣' },
  { label: '会员日', campaign_type: 'MEMBER_DAY', codePrefix: 'member_day', name: '会员日·老客回访', remark: '轻券回访，锚点卤蛋' },
  { label: '沉睡唤醒', campaign_type: 'WAKEUP', codePrefix: 'wakeup_sleep', name: '沉睡唤醒', remark: '配合 sleep30/sleep90 分群' },
  { label: '新客欢迎', campaign_type: 'NEW_MEMBER', codePrefix: 'new_member', name: '新客欢迎券', remark: '开卡/注册欢迎' },
]

const router = useRouter()
const auth = useAuthStore()
const { storeOptions, loadStores } = useStoreOptions()
const loading = ref(false)
const saving = ref(false)
const productsLoading = ref(false)
const items = ref<any[]>([])
const segments = ref<any[]>([])
const couponTemplates = ref<any[]>([])
const productOptions = ref<any[]>([])
const typeFilter = ref<string | undefined>()
/** 超管筛选门店；本店账号固定为本店 */
const filterStoreId = ref<number | undefined>()
const marketingEnabled = ref<boolean | null>(null)
const dlg = ref(false)
const taskDlg = ref(false)
const tasks = ref<any[]>([])
const taskStats = ref<Record<string, number> | null>(null)
const taskCampaignId = ref<number | null>(null)
const form = reactive<any>({
  id: null,
  store_id: null as number | null,
  code: '',
  name: '',
  campaign_type: 'MEMBER_DAY',
  segment_id: null,
  coupon_template_id: null,
  boost_product_ids: [] as number[],
  remark: '',
})

const boundStoreName = computed(() => {
  if (auth.isSuperAdmin) return ''
  const sid = auth.user?.store_id
  const s = storeOptions.value.find((x) => x.id === sid)
  return s?.name || auth.user?.store_name || (sid ? `门店#${sid}` : '')
})

const filteredItems = computed(() => {
  if (!typeFilter.value) return items.value
  return items.value.filter((x) => x.campaign_type === typeFilter.value)
})

/** 列表/商品/新建用的有效门店 */
function effectiveStoreId(): number | null | undefined {
  if (auth.isSuperAdmin) return filterStoreId.value ?? undefined
  return auth.user?.store_id ?? undefined
}

function goSegments() {
  router.push('/marketing/segments')
}
function goReport() {
  router.push('/marketing/report')
}
function goCoupons() {
  router.push('/coupons')
}
function goStoreBiz() {
  const sid = effectiveStoreId() || storeOptions.value[0]?.id
  router.push(sid ? { path: '/store-info', query: { store_id: String(sid), tab: 'biz' } } : '/store-info')
}

function storeLabel(id: number | null | undefined) {
  if (id == null) return '总部'
  const s = storeOptions.value.find((x) => x.id === id)
  return s?.name || `#${id}`
}

function typeLabel(code: string | null | undefined) {
  const t = CAMPAIGN_TYPES.find((x) => x.value === code)
  return t ? t.label.split(' ')[0] : (code || '—')
}

function typeHint(code: string | null | undefined) {
  const t = CAMPAIGN_TYPES.find((x) => x.value === code)
  return t?.hint || '草稿可反复改；发布后按分群快照批量发券。'
}

function segmentLabel(id: number | null | undefined) {
  if (id == null) return '—'
  const s = segments.value.find((x) => x.id === id)
  return s ? `${s.name} (#${id})` : `#${id}`
}

function couponLabel(id: number | null | undefined) {
  if (id == null) return '—'
  const t = couponTemplates.value.find((x) => x.id === id)
  return t ? `${t.name} (#${id})` : `#${id}`
}

function productOptionLabel(p: any) {
  const price = p.price != null ? ` ¥${p.price}` : ''
  return `${p.name}${price} (#${p.id})`
}

function boostProductsLabel(ids: unknown) {
  if (!Array.isArray(ids) || !ids.length) return '—'
  return ids
    .map((id) => {
      const p = productOptions.value.find((x) => x.id === id)
      return p ? p.name : `#${id}`
    })
    .join('、')
}

function normalizeBoostIds(ids: unknown): number[] {
  if (!Array.isArray(ids)) return []
  return ids.map((n) => Number(n)).filter((n) => Number.isInteger(n) && n > 0)
}

function todayTag() {
  const d = new Date()
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}`
}

async function loadProducts() {
  productsLoading.value = true
  try {
    const storeId = form.store_id || effectiveStoreId() || storeOptions.value[0]?.id
    const all: any[] = []
    let page = 1
    let totalPages = 1
    do {
      const res: any = await api.get('/products', {
        params: {
          page,
          page_size: 200,
          status: 'ON_SALE',
          ...(storeId ? { store_id: storeId } : {}),
        },
      })
      const data = res?.data || {}
      all.push(...(data.items || []))
      totalPages = Math.max(1, Number(data.total_pages || 1))
      page += 1
    } while (page <= totalPages && page <= 30)
    productOptions.value = all
  } catch {
    productOptions.value = []
  } finally {
    productsLoading.value = false
  }
}

async function load() {
  loading.value = true
  try {
    await loadStores()
    if (!auth.isSuperAdmin && auth.user?.store_id) {
      filterStoreId.value = auth.user.store_id
    }
    const sid = effectiveStoreId()
    const [c, s, t] = await Promise.all([
      api.get('/marketing/campaigns', {
        params: { page_size: 50, ...(sid ? { store_id: sid } : {}) },
      }),
      api.get('/marketing/segments', {
        params: { page_size: 100, ...(sid ? { store_id: sid } : {}) },
      }),
      api.get('/coupons/templates', { params: { include_disabled: false } }),
      loadProducts(),
    ])
    items.value = c.data?.items || []
    marketingEnabled.value =
      typeof c.data?.marketing_enabled === 'boolean' ? c.data.marketing_enabled : null
    segments.value = s.data?.items || []
    const raw = t.data?.items || t.data || []
    couponTemplates.value = Array.isArray(raw) ? raw : []
  } finally {
    loading.value = false
  }
}

async function ensureBoostProductsInOptions(ids: number[]) {
  for (const id of ids) {
    if (productOptions.value.some((p) => p.id === id)) continue
    try {
      const res = await api.get(`/products/${id}`)
      if (res.data?.id) productOptions.value.push(res.data)
    } catch {
      productOptions.value.push({ id, name: `商品#${id}` })
    }
  }
}

async function openEdit(row?: any) {
  if (marketingEnabled.value === false && !row) {
    ElMessage.warning('当前门店已关闭营销活动，请先到门店业务参数开启')
    return
  }
  if (row) {
    const boost = normalizeBoostIds(row.boost_product_ids)
    Object.assign(form, {
      id: row.id,
      store_id: row.store_id ?? null,
      code: row.code,
      name: row.name,
      campaign_type: row.campaign_type || 'MEMBER_DAY',
      segment_id: row.segment_id,
      coupon_template_id: row.coupon_template_id,
      boost_product_ids: boost,
      remark: row.remark || '',
    })
    await loadProducts()
    await ensureBoostProductsInOptions(boost)
  } else {
    const defaultStore = auth.isSuperAdmin
      ? (filterStoreId.value ?? null)
      : (auth.user?.store_id ?? null)
    Object.assign(form, {
      id: null,
      store_id: defaultStore,
      code: '',
      name: '',
      campaign_type: 'MEMBER_DAY',
      segment_id: null,
      coupon_template_id: null,
      boost_product_ids: [],
      remark: '',
    })
    await loadProducts()
  }
  dlg.value = true
}

async function openEditFromPreset(p: CampaignPreset) {
  if (marketingEnabled.value === false) {
    ElMessage.warning('当前门店已关闭营销活动，请先到门店业务参数开启')
    return
  }
  const defaultStore = auth.isSuperAdmin
    ? (filterStoreId.value ?? null)
    : (auth.user?.store_id ?? null)
  Object.assign(form, {
    id: null,
    store_id: defaultStore,
    code: `${p.codePrefix}_${todayTag()}`,
    name: p.name,
    campaign_type: p.campaign_type,
    segment_id: null,
    coupon_template_id: null,
    boost_product_ids: [],
    remark: p.remark,
  })
  await loadProducts()
  dlg.value = true
}

async function save() {
  if (!form.code?.trim() || !form.name?.trim()) {
    ElMessage.warning('请填写编码与名称')
    return
  }
  if (!auth.isSuperAdmin && !auth.user?.store_id) {
    ElMessage.warning('账号未绑定门店')
    return
  }
  const boost = normalizeBoostIds(form.boost_product_ids)
  saving.value = true
  try {
    const body = {
      code: form.code,
      name: form.name,
      campaign_type: form.campaign_type,
      segment_id: form.segment_id,
      coupon_template_id: form.coupon_template_id,
      boost_product_ids: boost.length ? boost : null,
      remark: form.remark || null,
      store_id: auth.isSuperAdmin ? (form.store_id || null) : (auth.user?.store_id || null),
    }
    if (form.id) await api.put(`/marketing/campaigns/${form.id}`, body)
    else await api.post('/marketing/campaigns', body)
    ElMessage.success('已保存')
    dlg.value = false
    await load()
  } finally {
    saving.value = false
  }
}

async function publish(row: any) {
  await ElMessageBox.confirm(
    `发布活动「${row.name}」并按分群生成发券任务？请确认分群已物化、券模板可用。`,
    '确认发布',
  )
  const res = await api.post(`/marketing/campaigns/${row.id}/publish`)
  ElMessage.success(`已入队 ${res.data?.enqueued ?? 0} 条任务`)
  await load()
}

async function setStatus(row: any, status: string) {
  await api.post(`/marketing/campaigns/${row.id}/status`, { status })
  ElMessage.success(`已切换为 ${status}`)
  await load()
}

async function showTasks(row: any) {
  taskCampaignId.value = row.id
  const res = await api.get(`/marketing/campaigns/${row.id}/tasks`, { params: { page_size: 50 } })
  tasks.value = res.data?.items || []
  taskStats.value = res.data?.task_stats || null
  taskDlg.value = true
}

async function retryFailed() {
  if (!taskCampaignId.value) return
  const res = await api.post(`/marketing/campaigns/${taskCampaignId.value}/tasks/retry-failed`)
  ElMessage.success(`已重置 ${res.data?.reset ?? 0} 条`)
  await showTasks({ id: taskCampaignId.value })
}

onMounted(load)
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
.sub { margin: 0; font-size: 13px; color: #888; line-height: 1.5; max-width: 640px; }
.header-actions { display: flex; gap: 8px; flex-wrap: wrap; flex-shrink: 0; }
.tip { margin-bottom: 10px; }
.preset-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}
.preset-label {
  font-size: 13px;
  color: #666;
  margin-right: 4px;
}
.filter-bar { margin-bottom: 12px; }
.hint { margin-top: 4px; font-size: 12px; color: #888; line-height: 1.4; }
</style>
