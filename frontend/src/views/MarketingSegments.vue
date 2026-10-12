<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2>客户分群</h2>
        <p class="sub">圈选目标顾客，供「营销活动」发券与触达。预置分群：沉睡30天 / 沉睡90天+ / 新会员7日 / 低余额 / 高积分活跃。</p>
      </div>
      <div class="header-actions">
        <el-button @click="goCampaigns">去营销活动</el-button>
        <el-button @click="load">刷新</el-button>
        <el-button type="primary" @click="openEdit()">新建分群</el-button>
      </div>
    </div>

    <el-alert
      type="info"
      show-icon
      :closable="false"
      class="tip"
      title="店长怎么用"
      description="① 新建或选用预置分群 → ②「估人数」看规模（脱敏样本）→ ③「物化」生成成员快照 → ④ 到「营销活动」选该分群发布发券。动态分群按规则实时算；物化后快照供本轮活动稳定圈人。"
    />
    <el-alert
      type="warning"
      show-icon
      :closable="false"
      class="tip"
      title="规则字段（JSON）"
      description="支持 field：member_status / days_since_order / days_since_member / points / balance_total / has_order / status / account_status / registered_store_id。运算符：eq / gt / gte / lt / lte。结构示例：{&quot;all&quot;:[{&quot;field&quot;:&quot;member_status&quot;,&quot;op&quot;:&quot;eq&quot;,&quot;value&quot;:&quot;MEMBER&quot;}]}。导出与估人数均无明文手机。"
    />

    <div class="filter-bar">
      <el-select
        v-if="auth.isSuperAdmin"
        v-model="filterStoreId"
        clearable
        placeholder="门店（空=全部/含总部）"
        style="width: 200px"
        @change="load"
      >
        <el-option v-for="s in storeOptions" :key="s.id" :label="s.name" :value="s.id" />
      </el-select>
      <el-tag v-else-if="boundStoreName" type="info">门店：{{ boundStoreName }}</el-tag>
    </div>

    <el-empty v-if="!loading && !items.length" description="暂无分群；可新建，或确认已导入 data_custom 预置分群" />
    <el-table v-else :data="items" v-loading="loading" stripe>
      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column label="门店" width="120" show-overflow-tooltip>
        <template #default="{ row }">{{ storeLabel(row.store_id) }}</template>
      </el-table-column>
      <el-table-column prop="code" label="编码" width="140" />
      <el-table-column prop="name" label="名称" min-width="140" />
      <el-table-column prop="segment_type" label="类型" width="100" />
      <el-table-column prop="estimated_count" label="估人数" width="90" />
      <el-table-column prop="status" label="状态" width="80">
        <template #default="{ row }">{{ row.status === 1 ? '启用' : '停用' }}</template>
      </el-table-column>
      <el-table-column label="操作" width="280" fixed="right">
        <template #default="{ row }">
          <el-button size="small" @click="openEdit(row)">编辑</el-button>
          <el-button size="small" @click="estimate(row)">估人数</el-button>
          <el-button size="small" type="primary" @click="materialize(row)">物化</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dlg" :title="form.id ? '编辑分群' : '新建分群'" width="600px" destroy-on-close>
      <el-alert
        type="info"
        :closable="false"
        show-icon
        style="margin-bottom:12px"
        title="编码建议用英文业务键（如 sleep30）。保存后可估人数；发券前建议先物化。"
      />
      <el-form label-width="100px">
        <el-form-item v-if="auth.isSuperAdmin" label="归属门店">
          <el-select v-model="form.store_id" clearable placeholder="总部（全店共享）" style="width:100%" :disabled="!!form.id">
            <el-option v-for="s in storeOptions" :key="s.id" :label="s.name" :value="s.id" />
          </el-select>
        </el-form-item>
        <el-form-item v-else label="归属门店">
          <el-tag type="info">{{ boundStoreName || '本店' }}</el-tag>
        </el-form-item>
        <el-form-item label="编码"><el-input v-model="form.code" placeholder="如 sleep30" /></el-form-item>
        <el-form-item label="名称"><el-input v-model="form.name" placeholder="如 沉睡30天" /></el-form-item>
        <el-form-item label="类型">
          <el-select v-model="form.segment_type" style="width:100%">
            <el-option label="动态 DYNAMIC（按规则实时算）" value="DYNAMIC" />
            <el-option label="静态 STATIC（依赖物化快照）" value="STATIC" />
          </el-select>
        </el-form-item>
        <el-form-item label="规则 JSON">
          <el-input
            v-model="rulesText"
            type="textarea"
            :rows="8"
            placeholder='{"all":[{"field":"member_status","op":"eq","value":"MEMBER"}]}'
          />
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="form.status" :active-value="1" :inactive-value="0" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dlg = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="estDlg" title="估人数结果" width="640px">
      <el-alert
        type="info"
        :closable="false"
        show-icon
        style="margin-bottom:12px"
        title="样本仅脱敏预览，手机号已打码；完整圈人以物化快照 / 发券任务为准。"
      />
      <p>命中约 <b>{{ est.count }}</b> 人（脱敏样本最多 20）</p>
      <el-table :data="est.samples" size="small" stripe>
        <el-table-column prop="id" label="ID" width="70" />
        <el-table-column prop="customer_no" label="编号" width="120" />
        <el-table-column prop="display_name" label="昵称" />
        <el-table-column prop="phone_masked" label="手机" width="120" />
        <el-table-column prop="member_status" label="状态" width="90" />
      </el-table>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '@/api'
import { useAuthStore } from '@/stores/auth'
import { useStoreOptions } from '@/composables/useStoreOptions'

const router = useRouter()
const auth = useAuthStore()
const { storeOptions, loadStores } = useStoreOptions()
const loading = ref(false)
const saving = ref(false)
const items = ref<any[]>([])
const filterStoreId = ref<number | undefined>()
const dlg = ref(false)
const estDlg = ref(false)
const rulesText = ref('{"all":[{"field":"member_status","op":"eq","value":"MEMBER"}]}')
const form = reactive<any>({
  id: null,
  store_id: null as number | null,
  code: '',
  name: '',
  segment_type: 'DYNAMIC',
  status: 1,
})
const est = reactive<{ count: number; samples: any[] }>({ count: 0, samples: [] })

const boundStoreName = computed(() => {
  if (auth.isSuperAdmin) return ''
  const sid = auth.user?.store_id
  const s = storeOptions.value.find((x) => x.id === sid)
  return s?.name || auth.user?.store_name || (sid ? `门店#${sid}` : '')
})

function storeLabel(id: number | null | undefined) {
  if (id == null) return '总部'
  const s = storeOptions.value.find((x) => x.id === id)
  return s?.name || `#${id}`
}

function goCampaigns() {
  router.push('/marketing/campaigns')
}

async function load() {
  loading.value = true
  try {
    await loadStores()
    if (!auth.isSuperAdmin && auth.user?.store_id) filterStoreId.value = auth.user.store_id
    const sid = auth.isSuperAdmin ? filterStoreId.value : auth.user?.store_id
    const res = await api.get('/marketing/segments', {
      params: { page_size: 50, ...(sid ? { store_id: sid } : {}) },
    })
    items.value = res.data?.items || []
  } finally {
    loading.value = false
  }
}

function openEdit(row?: any) {
  if (row) {
    form.id = row.id
    form.store_id = row.store_id ?? null
    form.code = row.code
    form.name = row.name
    form.segment_type = row.segment_type || 'DYNAMIC'
    form.status = row.status
    rulesText.value = JSON.stringify(row.rules_json || { all: [] }, null, 2)
  } else {
    form.id = null
    form.store_id = auth.isSuperAdmin
      ? (filterStoreId.value ?? null)
      : (auth.user?.store_id ?? null)
    form.code = ''
    form.name = ''
    form.segment_type = 'DYNAMIC'
    form.status = 1
    rulesText.value = '{"all":[{"field":"member_status","op":"eq","value":"MEMBER"}]}'
  }
  dlg.value = true
}

async function save() {
  let rules: any
  try {
    rules = JSON.parse(rulesText.value || '{}')
  } catch {
    ElMessage.error('规则 JSON 无效')
    return
  }
  saving.value = true
  try {
    const body = {
      code: form.code,
      name: form.name,
      segment_type: form.segment_type,
      rules_json: rules,
      status: form.status,
      store_id: auth.isSuperAdmin ? (form.store_id || null) : (auth.user?.store_id || null),
    }
    if (form.id) await api.put(`/marketing/segments/${form.id}`, body)
    else await api.post('/marketing/segments', body)
    ElMessage.success('已保存')
    dlg.value = false
    await load()
  } finally {
    saving.value = false
  }
}

async function estimate(row: any) {
  const sid = row.store_id || filterStoreId.value || auth.user?.store_id
  const res = await api.post('/marketing/segments/estimate', {
    rules_json: row.rules_json,
    sample_limit: 20,
    store_id: sid || null,
  })
  est.count = res.data?.count || 0
  est.samples = res.data?.samples || []
  estDlg.value = true
}

async function materialize(row: any) {
  const res = await api.post(`/marketing/segments/${row.id}/materialize`)
  ElMessage.success(`已物化 ${res.data?.count ?? 0} 人；可去营销活动选用该分群`)
  await load()
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
.sub { margin: 0; font-size: 13px; color: #888; line-height: 1.5; max-width: 720px; }
.header-actions { display: flex; gap: 8px; flex-shrink: 0; }
.tip { margin-bottom: 10px; }
.filter-bar { margin-bottom: 12px; display: flex; gap: 8px; align-items: center; }
</style>
