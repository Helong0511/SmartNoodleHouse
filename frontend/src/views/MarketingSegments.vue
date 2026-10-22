<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2>客户分群</h2>
        <p class="sub">
          圈选目标顾客，供「营销活动」发券与触达。预置：沉睡/新客/低余额、生命周期/RFM、今日生日、今日入会周年（无生日）、公益身份（教师/长者/退役军人/环卫）。
        </p>
      </div>
      <div class="header-actions">
        <el-button @click="goCampaigns">去营销活动</el-button>
        <el-button @click="load">刷新</el-button>
        <el-button v-if="canManageSegments" type="primary" @click="openEdit()">新建分群</el-button>
      </div>
    </div>

    <el-alert
      type="info"
      show-icon
      :closable="false"
      class="tip"
      title="店长怎么用"
      description="① 选用预置或新建分群 → ②「估人数」看规模（脱敏）→ ③ 本店自建分群可「物化」；总部预置由超管物化，门店发布活动时会按本店自动圈人 → ④ 到「营销活动」选该分群发布。门店与总部可同编码并存（并行资源，互不覆盖）。"
    />

    <el-button link type="primary" class="help-toggle" @click="helpOpen = !helpOpen">
      {{ helpOpen ? '收起名词说明' : '生命周期 / RFM / 字段说明（点开）' }}
    </el-button>
    <el-collapse v-if="helpOpen" v-model="helpActive" class="help-box">
      <el-collapse-item title="生命周期是什么？" name="lc">
        <p class="help-p">{{ LIFECYCLE_OVERVIEW }}</p>
        <ul class="help-ul">
          <li v-for="(txt, code) in LIFECYCLE_CODE_HELP" :key="code">
            <b>{{ label(CUSTOMER_LIFECYCLE, code) }}（{{ code }}）</b> — {{ txt.replace(/^[^：]+：/, '') }}
          </li>
        </ul>
      </el-collapse-item>
      <el-collapse-item title="RFM 是什么？" name="rfm">
        <p class="help-p">{{ RFM_OVERVIEW }}</p>
        <ul class="help-ul">
          <li v-for="(txt, code) in RFM_SEGMENT_HELP" :key="code">
            <b>{{ label(CUSTOMER_RFM, code) }}（{{ code }}）</b> — {{ txt.replace(/^[^：]+：/, '') }}
          </li>
        </ul>
      </el-collapse-item>
      <el-collapse-item title="动态 / 静态 / 物化" name="mat">
        <p class="help-p">{{ SEGMENT_TYPE_HELP }}</p>
        <p class="help-p">{{ MATERIALIZE_HELP }}</p>
      </el-collapse-item>
      <el-collapse-item title="规则字段一览" name="fields">
        <ul class="help-ul">
          <li v-for="f in RULE_FIELDS" :key="f.value">
            <b>{{ f.label }}（{{ f.value }}）</b> — {{ RULE_FIELD_HELP[f.value] || '' }}
          </li>
        </ul>
      </el-collapse-item>
    </el-collapse>

    <div v-if="canManageSegments" class="preset-bar">
      <span class="preset-label">快捷规则</span>
      <el-button
        v-for="p in RULE_PRESETS"
        :key="p.code"
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
      <el-table-column prop="code" label="编码" width="150" show-overflow-tooltip />
      <el-table-column prop="name" label="名称" min-width="140" show-overflow-tooltip />
      <el-table-column label="类型" width="90">
        <template #default="{ row }">{{ label(SEGMENT_TYPE, row.segment_type) }}</template>
      </el-table-column>
      <el-table-column prop="estimated_count" label="估人数" width="90" />
      <el-table-column label="物化时间" width="160">
        <template #default="{ row }">{{ row.materialized_at || '—' }}</template>
      </el-table-column>
      <el-table-column label="状态" width="80">
        <template #default="{ row }">
          <el-tag size="small" :type="row.status === 1 ? 'success' : 'info'">
            {{ row.status === 1 ? '启用' : '停用' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="280" fixed="right">
        <template #default="{ row }">
          <el-button size="small" :disabled="!canEditRow(row)" @click="openEdit(row)">编辑</el-button>
          <el-button size="small" @click="estimate(row)">估人数</el-button>
          <el-button
            size="small"
            type="primary"
            :disabled="!canMaterializeRow(row)"
            @click="materialize(row)"
          >物化</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dlg" :title="form.id ? '编辑分群' : '新建分群'" width="720px" destroy-on-close>
      <el-alert
        type="info"
        :closable="false"
        show-icon
        style="margin-bottom:12px"
        title="编码建议用英文业务键（如 sleep30）。可视化条件默认「且」关系；高级 JSON 支持 all/any 嵌套。"
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
            <el-option label="动态（按规则实时算）" value="DYNAMIC" />
            <el-option label="静态（依赖物化快照）" value="STATIC" />
          </el-select>
          <div class="hint">{{ SEGMENT_TYPE_HELP }}</div>
        </el-form-item>
        <el-form-item label="规则编辑">
          <el-radio-group v-model="ruleMode" size="small">
            <el-radio-button value="visual">可视化</el-radio-button>
            <el-radio-button value="json">高级 JSON</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="ruleMode === 'visual'" label="条件（且）">
          <div class="rule-list">
            <div v-for="(c, idx) in visualRules" :key="idx" class="rule-block">
              <div class="rule-row">
                <el-select
                  v-model="c.field"
                  placeholder="字段"
                  style="width: 160px"
                  filterable
                  @change="onFieldChange(c)"
                >
                  <el-option
                    v-for="f in RULE_FIELDS"
                    :key="f.value"
                    :label="f.label"
                    :value="f.value"
                  >
                    <span>{{ f.label }}</span>
                    <span class="opt-code">{{ f.value }}</span>
                  </el-option>
                </el-select>
                <el-select v-model="c.op" placeholder="运算" style="width: 100px">
                  <el-option v-for="o in RULE_OPS" :key="o.value" :label="o.label" :value="o.value" />
                </el-select>
                <el-select
                  v-if="valueOptions(c.field)"
                  v-model="c.value"
                  placeholder="取值"
                  style="flex: 1"
                  filterable
                >
                  <el-option
                    v-for="opt in valueOptions(c.field)"
                    :key="opt.value"
                    :label="opt.label"
                    :value="opt.value"
                  />
                </el-select>
                <el-input
                  v-else
                  v-model="c.value"
                  :placeholder="valuePlaceholder(c.field)"
                  style="flex: 1"
                />
                <el-button link type="danger" @click="removeRule(idx)">删</el-button>
              </div>
              <div v-if="RULE_FIELD_HELP[c.field]" class="hint">{{ RULE_FIELD_HELP[c.field] }}</div>
            </div>
            <el-button size="small" @click="addRule">添加条件</el-button>
          </div>
        </el-form-item>
        <el-form-item v-else label="规则 JSON">
          <el-input
            v-model="rulesText"
            type="textarea"
            :rows="10"
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
        <el-table-column prop="customer_no" label="编号" width="120" show-overflow-tooltip />
        <el-table-column label="展示名" min-width="100" show-overflow-tooltip>
          <template #default="{ row }">{{ row.display_name || '—' }}</template>
        </el-table-column>
        <el-table-column label="昵称" min-width="100" show-overflow-tooltip>
          <template #default="{ row }">{{ row.nickname || '—' }}</template>
        </el-table-column>
        <el-table-column prop="phone_masked" label="手机" width="120" />
        <el-table-column label="状态" width="90">
          <template #default="{ row }">{{ label(MEMBER_STATUS, row.member_status) }}</template>
        </el-table-column>
      </el-table>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '@/api'
import { useAuthStore } from '@/stores/auth'
import { useStoreOptions } from '@/composables/useStoreOptions'
import { notifySaveWithOverride } from '@/utils/overrideWarning'
import {
  CUSTOMER_LIFECYCLE,
  CUSTOMER_RFM,
  MEMBER_STATUS,
  SEGMENT_TYPE,
  SOCIAL_IDENTITY,
  label,
} from '@/utils/labels'
import {
  LIFECYCLE_CODE_HELP,
  LIFECYCLE_OVERVIEW,
  MATERIALIZE_HELP,
  RFM_OVERVIEW,
  RFM_SEGMENT_HELP,
  RULE_FIELD_HELP,
  SEGMENT_TYPE_HELP,
} from '@/utils/marketingHelp'

type RuleCond = { field: string; op: string; value: string }

type RulePreset = {
  label: string
  code: string
  name: string
  rules: RuleCond[]
}

const RULE_FIELDS = [
  { value: 'member_status', label: '会员身份' },
  { value: 'days_since_order', label: '距上次下单(天)' },
  { value: 'days_since_member', label: '入会天数' },
  { value: 'points', label: '积分' },
  { value: 'balance_total', label: '储值余额' },
  { value: 'has_order', label: '是否下过单' },
  { value: 'lifecycle_code', label: '生命周期' },
  { value: 'rfm_segment', label: 'RFM分群' },
  { value: 'silence_days', label: '沉默天数(夜批)' },
  { value: 'account_status', label: '账号状态' },
  { value: 'status', label: '启用状态' },
  { value: 'registered_store_id', label: '注册门店ID' },
  { value: 'social_identity_code', label: '公益身份码' },
  { value: 'is_birthday_today', label: '今日是否生日' },
  { value: 'is_member_anniversary_today', label: '今日是否入会周年（无生日）' },
]

const helpOpen = ref(false)
const helpActive = ref(['lc', 'rfm'])

function valueOptions(field: string): { value: string; label: string }[] | null {
  if (field === 'member_status') {
    return Object.entries(MEMBER_STATUS).map(([value, lab]) => ({ value, label: `${lab}（${value}）` }))
  }
  if (field === 'lifecycle_code') {
    return Object.entries(CUSTOMER_LIFECYCLE).map(([value, lab]) => ({ value, label: `${lab}（${value}）` }))
  }
  if (field === 'rfm_segment') {
    return Object.entries(CUSTOMER_RFM).map(([value, lab]) => ({ value, label: `${lab}（${value}）` }))
  }
  if (field === 'has_order') {
    return [
      { value: 'true', label: '是（下过单）' },
      { value: 'false', label: '否（从未下单）' },
    ]
  }
  if (field === 'status') {
    return [
      { value: '1', label: '启用（1）' },
      { value: '0', label: '停用（0）' },
    ]
  }
  if (field === 'account_status') {
    return [
      { value: 'ACTIVE', label: '正常 ACTIVE' },
      { value: 'DISABLED', label: '禁用 DISABLED' },
    ]
  }
  if (field === 'social_identity_code') {
    return Object.entries(SOCIAL_IDENTITY).map(([value, lab]) => ({ value, label: `${lab}（${value}）` }))
  }
  if (field === 'is_birthday_today') {
    return [
      { value: 'true', label: '是（今日生日）' },
      { value: 'false', label: '否' },
    ]
  }
  if (field === 'is_member_anniversary_today') {
    return [
      { value: 'true', label: '是（今日入会周年，且未填生日）' },
      { value: 'false', label: '否' },
    ]
  }
  return null
}

function valuePlaceholder(field: string) {
  if (field === 'days_since_order' || field === 'days_since_member' || field === 'silence_days') return '天数，如 30'
  if (field === 'points') return '积分，如 500'
  if (field === 'balance_total') return '余额元，如 20'
  if (field === 'registered_store_id') return '门店数字 ID'
  return '值'
}

function onFieldChange(c: RuleCond) {
  const opts = valueOptions(c.field)
  if (opts?.length) {
    c.value = opts[0].value
    if (
      c.field === 'lifecycle_code' ||
      c.field === 'rfm_segment' ||
      c.field === 'member_status' ||
      c.field === 'social_identity_code' ||
      c.field === 'is_birthday_today' ||
      c.field === 'is_member_anniversary_today'
    ) {
      c.op = 'eq'
    }
  } else if (['days_since_order', 'silence_days'].includes(c.field)) {
    c.op = 'gte'
    c.value = '30'
  } else {
    c.value = ''
  }
}

const RULE_OPS = [
  { value: 'eq', label: '=' },
  { value: 'ne', label: '≠' },
  { value: 'gt', label: '>' },
  { value: 'gte', label: '≥' },
  { value: 'lt', label: '<' },
  { value: 'lte', label: '≤' },
]

const RULE_PRESETS: RulePreset[] = [
  {
    label: '沉睡30天',
    code: 'sleep30',
    name: '沉睡30天',
    rules: [
      { field: 'member_status', op: 'eq', value: 'MEMBER' },
      { field: 'days_since_order', op: 'gte', value: '30' },
      { field: 'days_since_order', op: 'lt', value: '90' },
    ],
  },
  {
    label: '沉睡90天+',
    code: 'sleep90',
    name: '沉睡90天+',
    rules: [
      { field: 'member_status', op: 'eq', value: 'MEMBER' },
      { field: 'days_since_order', op: 'gte', value: '90' },
    ],
  },
  {
    label: '新会员7日',
    code: 'new_member',
    name: '新会员7日',
    rules: [
      { field: 'member_status', op: 'eq', value: 'MEMBER' },
      { field: 'days_since_member', op: 'lte', value: '7' },
    ],
  },
  {
    label: '低余额',
    code: 'low_balance',
    name: '低余额会员',
    rules: [
      { field: 'member_status', op: 'eq', value: 'MEMBER' },
      { field: 'balance_total', op: 'gt', value: '0' },
      { field: 'balance_total', op: 'lt', value: '20' },
    ],
  },
  {
    label: '风险客',
    code: 'lifecycle_at_risk',
    name: '生命周期·风险客',
    rules: [
      { field: 'member_status', op: 'eq', value: 'MEMBER' },
      { field: 'lifecycle_code', op: 'eq', value: 'AT_RISK' },
    ],
  },
  {
    label: '沉睡客',
    code: 'lifecycle_dormant',
    name: '生命周期·沉睡客',
    rules: [
      { field: 'member_status', op: 'eq', value: 'MEMBER' },
      { field: 'lifecycle_code', op: 'eq', value: 'DORMANT' },
    ],
  },
  {
    label: '流失客',
    code: 'lifecycle_lost',
    name: '生命周期·流失客',
    rules: [
      { field: 'member_status', op: 'eq', value: 'MEMBER' },
      { field: 'lifecycle_code', op: 'eq', value: 'LOST' },
    ],
  },
  {
    label: 'RFM休眠',
    code: 'rfm_hibernating',
    name: 'RFM·休眠',
    rules: [
      { field: 'member_status', op: 'eq', value: 'MEMBER' },
      { field: 'rfm_segment', op: 'eq', value: 'HIBERNATING' },
    ],
  },
  {
    label: 'RFM冠军',
    code: 'rfm_champions',
    name: 'RFM·冠军',
    rules: [
      { field: 'member_status', op: 'eq', value: 'MEMBER' },
      { field: 'rfm_segment', op: 'eq', value: 'CHAMPIONS' },
    ],
  },
  {
    label: '教师身份',
    code: 'social_teacher',
    name: '公益身份·教师',
    rules: [
      { field: 'member_status', op: 'eq', value: 'MEMBER' },
      { field: 'social_identity_code', op: 'eq', value: 'TEACHER' },
    ],
  },
  {
    label: '长者身份',
    code: 'social_elder',
    name: '公益身份·长者',
    rules: [
      { field: 'member_status', op: 'eq', value: 'MEMBER' },
      { field: 'social_identity_code', op: 'eq', value: 'ELDER' },
    ],
  },
  {
    label: '退役军人',
    code: 'social_veteran',
    name: '公益身份·退役军人',
    rules: [
      { field: 'member_status', op: 'eq', value: 'MEMBER' },
      { field: 'social_identity_code', op: 'eq', value: 'VETERAN' },
    ],
  },
  {
    label: '环卫工',
    code: 'social_sanitation',
    name: '公益身份·环卫工',
    rules: [
      { field: 'member_status', op: 'eq', value: 'MEMBER' },
      { field: 'social_identity_code', op: 'eq', value: 'SANITATION' },
    ],
  },
  {
    label: '今日生日',
    code: 'birthday_today',
    name: '今日生日会员',
    rules: [
      { field: 'member_status', op: 'eq', value: 'MEMBER' },
      { field: 'is_birthday_today', op: 'eq', value: 'true' },
    ],
  },
  {
    label: '入会周年',
    code: 'member_anniversary_today',
    name: '今日入会周年（未填生日）',
    rules: [
      { field: 'member_status', op: 'eq', value: 'MEMBER' },
      { field: 'is_member_anniversary_today', op: 'eq', value: 'true' },
    ],
  },
]

const router = useRouter()
const auth = useAuthStore()
const { storeOptions, loadStores } = useStoreOptions()
const loading = ref(false)
const saving = ref(false)
const items = ref<any[]>([])
const filterStoreId = ref<number | undefined>()
const dlg = ref(false)
const estDlg = ref(false)
const ruleMode = ref<'visual' | 'json'>('visual')
const rulesText = ref('{"all":[{"field":"member_status","op":"eq","value":"MEMBER"}]}')
const visualRules = ref<RuleCond[]>([{ field: 'member_status', op: 'eq', value: 'MEMBER' }])
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

const canManageSegments = computed(
  () => auth.isSuperAdmin || auth.hasPermission('coupon:manage'),
)

function canEditRow(row: any) {
  if (!canManageSegments.value) return false
  if (auth.isSuperAdmin) return true
  return row.store_id != null && row.store_id === auth.user?.store_id
}

function canMaterializeRow(row: any) {
  if (!canManageSegments.value) return false
  if (row.status !== 1) return false
  if (auth.isSuperAdmin) return true
  return row.store_id != null && row.store_id === auth.user?.store_id
}

function goCampaigns() {
  router.push('/marketing/campaigns')
}

function addRule() {
  visualRules.value.push({ field: 'member_status', op: 'eq', value: 'MEMBER' })
}

function removeRule(idx: number) {
  visualRules.value.splice(idx, 1)
  if (!visualRules.value.length) addRule()
}

function parseValue(raw: string, field: string): any {
  const t = (raw ?? '').trim()
  if (field === 'has_order' || field === 'is_birthday_today' || field === 'is_member_anniversary_today') {
    return ['1', 'true', 'yes', '是'].includes(t.toLowerCase())
  }
  if (
    ['points', 'balance_total', 'days_since_order', 'days_since_member', 'silence_days', 'status', 'registered_store_id'].includes(
      field,
    )
  ) {
    if (t === '') return 0
    const n = Number(t)
    return Number.isFinite(n) ? n : t
  }
  return t
}

function rulesFromVisual(): Record<string, any> {
  const all = visualRules.value
    .filter((c) => c.field && c.op)
    .map((c) => ({
      field: c.field,
      op: c.op,
      value: parseValue(c.value, c.field),
    }))
  return { all }
}

function visualFromRules(rules: any): RuleCond[] {
  const list = Array.isArray(rules?.all)
    ? rules.all
    : Array.isArray(rules)
      ? rules
      : []
  const rows = list
    .filter((c: any) => c && typeof c === 'object' && c.field)
    .map((c: any) => ({
      field: String(c.field),
      op: String(c.op || 'eq'),
      value: c.value == null ? '' : String(c.value),
    }))
  return rows.length ? rows : [{ field: 'member_status', op: 'eq', value: 'MEMBER' }]
}

watch(ruleMode, (mode, prev) => {
  if (mode === 'json' && prev === 'visual') {
    rulesText.value = JSON.stringify(rulesFromVisual(), null, 2)
  } else if (mode === 'visual' && prev === 'json') {
    try {
      visualRules.value = visualFromRules(JSON.parse(rulesText.value || '{}'))
    } catch {
      ElMessage.warning('JSON 无法解析，已保留原可视化条件')
      ruleMode.value = 'json'
    }
  }
})

async function load() {
  loading.value = true
  try {
    await loadStores()
    if (!auth.isSuperAdmin && auth.user?.store_id) filterStoreId.value = auth.user.store_id
    const sid = auth.isSuperAdmin ? filterStoreId.value : auth.user?.store_id
    const res = await api.get('/marketing/segments', {
      params: { page_size: 100, ...(sid ? { store_id: sid } : {}) },
    })
    items.value = res.data?.items || []
  } catch (e: any) {
    ElMessage.error(e?.message || '加载分群失败')
  } finally {
    loading.value = false
  }
}

function openEdit(row?: any) {
  if (!canManageSegments.value) {
    ElMessage.warning('缺少权限：coupon:manage（仅可查看与估人数）')
    return
  }
  if (row && !canEditRow(row)) {
    ElMessage.warning('总部预置分群仅超管可编辑；可估人数，发布活动时按本店自动圈人')
    return
  }
  ruleMode.value = 'visual'
  if (row) {
    form.id = row.id
    form.store_id = row.store_id ?? null
    form.code = row.code
    form.name = row.name
    form.segment_type = row.segment_type || 'DYNAMIC'
    form.status = row.status
    const rules = row.rules_json || { all: [] }
    rulesText.value = JSON.stringify(rules, null, 2)
    visualRules.value = visualFromRules(rules)
  } else {
    form.id = null
    form.store_id = auth.isSuperAdmin
      ? (filterStoreId.value ?? null)
      : (auth.user?.store_id ?? null)
    form.code = ''
    form.name = ''
    form.segment_type = 'DYNAMIC'
    form.status = 1
    visualRules.value = [{ field: 'member_status', op: 'eq', value: 'MEMBER' }]
    rulesText.value = JSON.stringify(rulesFromVisual(), null, 2)
  }
  dlg.value = true
}

function openEditFromPreset(p: RulePreset) {
  if (!canManageSegments.value) {
    ElMessage.warning('缺少权限：coupon:manage')
    return
  }
  form.id = null
  form.store_id = auth.isSuperAdmin
    ? (filterStoreId.value ?? null)
    : (auth.user?.store_id ?? null)
  form.code = `${p.code}_${Date.now().toString(36).slice(-4)}`
  form.name = p.name
  form.segment_type = 'DYNAMIC'
  form.status = 1
  visualRules.value = p.rules.map((r) => ({ ...r }))
  ruleMode.value = 'visual'
  rulesText.value = JSON.stringify(rulesFromVisual(), null, 2)
  dlg.value = true
}

async function save() {
  let rules: any
  try {
    if (ruleMode.value === 'visual') {
      rules = rulesFromVisual()
      if (!rules.all?.length) {
        ElMessage.error('请至少添加一条条件')
        return
      }
    } else {
      rules = JSON.parse(rulesText.value || '{}')
    }
  } catch {
    ElMessage.error('规则 JSON 无效')
    return
  }
  if (!form.code?.trim() || !form.name?.trim()) {
    ElMessage.warning('请填写编码与名称')
    return
  }
  saving.value = true
  try {
    const body = {
      code: form.code.trim(),
      name: form.name.trim(),
      segment_type: form.segment_type,
      rules_json: rules,
      status: form.status,
      store_id: auth.isSuperAdmin ? (form.store_id || null) : (auth.user?.store_id || null),
    }
    const res = form.id
      ? await api.put(`/marketing/segments/${form.id}`, body)
      : await api.post('/marketing/segments', body)
    notifySaveWithOverride(res)
    dlg.value = false
    await load()
  } catch (e: any) {
    ElMessage.error(e?.message || '保存失败')
  } finally {
    saving.value = false
  }
}

async function estimate(row: any) {
  try {
    const sid = row.store_id || filterStoreId.value || auth.user?.store_id
    const res = await api.post('/marketing/segments/estimate', {
      rules_json: row.rules_json,
      sample_limit: 20,
      store_id: sid || null,
    })
    est.count = res.data?.count || 0
    est.samples = res.data?.samples || []
    estDlg.value = true
  } catch (e: any) {
    ElMessage.error(e?.message || '估人数失败')
  }
}

async function materialize(row: any) {
  if (!canMaterializeRow(row)) {
    ElMessage.warning('总部预置分群仅超管可物化；门店发布活动时会按本店自动圈人')
    return
  }
  try {
    const res = await api.post(`/marketing/segments/${row.id}/materialize`)
    ElMessage.success(`已物化 ${res.data?.count ?? 0} 人；可去营销活动选用该分群`)
    await load()
  } catch (e: any) {
    ElMessage.error(e?.message || '物化失败')
  }
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
.sub { margin: 0; font-size: 13px; color: #888; line-height: 1.5; max-width: 760px; }
.header-actions { display: flex; gap: 8px; flex-shrink: 0; flex-wrap: wrap; }
.tip { margin-bottom: 10px; }
.help-toggle { margin: 0 0 8px; padding: 0; }
.help-box {
  margin-bottom: 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  padding: 0 12px;
  background: #fafcff;
}
.help-p {
  margin: 0 0 8px;
  font-size: 13px;
  line-height: 1.65;
  color: #555;
  white-space: pre-line;
}
.help-ul {
  margin: 0;
  padding-left: 18px;
  font-size: 13px;
  line-height: 1.65;
  color: #444;
}
.filter-bar { margin-bottom: 12px; display: flex; gap: 8px; align-items: center; }
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
.rule-list {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.rule-block {
  display: flex;
  flex-direction: column;
  gap: 4px;
  width: 100%;
}
.rule-row {
  display: flex;
  gap: 8px;
  align-items: center;
  width: 100%;
}
.hint { margin-top: 2px; font-size: 12px; color: #888; line-height: 1.45; }
.opt-code { float: right; color: #99a; font-size: 12px; margin-left: 8px; }
</style>
