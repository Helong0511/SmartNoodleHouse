<template>
  <div v-loading="loading" class="store-biz">
    <el-alert
      type="warning"
      show-icon
      :closable="false"
      class="top-alert"
      :title="storeTitle"
      description="以下参数只写入本店，扫码该店后生效。未单独设置的项沿用平台默认，可清除后回退。"
    />

    <el-empty v-if="!storeId" description="请选择门店" />
    <el-empty v-else-if="!loading && !storeGroups.length" description="暂无可按门店管理的配置项" />

    <el-tabs
      v-else
      v-model="innerTab"
      class="biz-tabs"
      @tab-change="onInnerTab"
    >
      <el-tab-pane
        v-for="g in storeGroups"
        :key="g.id"
        :name="g.id"
      >
        <template #label>
          <span class="tab-label">
            {{ g.label }}
            <el-badge v-if="dirtyCountByGroup[g.id]" :value="dirtyCountByGroup[g.id]" class="tab-badge" />
          </span>
        </template>

        <el-alert
          type="info"
          show-icon
          :closable="false"
          :title="g.label"
          :description="g.description || ''"
          class="group-alert"
        />

        <el-form label-width="180px" class="business-form" @submit.prevent>
          <el-form-item v-for="item in itemsOfGroup(g.id)" :key="item.key" :label="item.label">
            <el-switch v-if="item.type === 'bool'" v-model="item.boolValue" />
            <el-select
              v-else-if="isCouponNameSelect(item.key)"
              v-model="item.strValue"
              filterable
              clearable
              placeholder="不赠券 / 关闭"
              style="max-width: 520px; width: 100%"
            >
              <el-option-group v-if="couponOptions(item, 'STORE').length" label="本店券模板">
                <el-option
                  v-for="opt in couponOptions(item, 'STORE')"
                  :key="`store-${opt.value}`"
                  :label="opt.label"
                  :value="opt.value"
                />
              </el-option-group>
              <el-option-group v-if="couponOptions(item, 'HQ').length" label="总部券模板">
                <el-option
                  v-for="opt in couponOptions(item, 'HQ')"
                  :key="`hq-${opt.value}`"
                  :label="opt.label"
                  :value="opt.value"
                />
              </el-option-group>
              <el-option
                v-for="opt in couponOptions(item, 'CURRENT')"
                :key="`cur-${opt.value}`"
                :label="opt.label"
                :value="opt.value"
              />
            </el-select>
            <el-select
              v-else-if="item.key === 'welcome_bundle_template_ids'"
              v-model="item.idListValue"
              multiple
              filterable
              clearable
              collapse-tags
              collapse-tags-tooltip
              placeholder="不选则回退「新客赠券模板名」"
              style="max-width: 560px; width: 100%"
              @change="onBundleIdsChange(item)"
            >
              <el-option-group v-if="couponOptions(item, 'STORE').length" label="本店券模板">
                <el-option
                  v-for="opt in couponOptions(item, 'STORE')"
                  :key="`b-store-${opt.value}`"
                  :label="opt.label"
                  :value="opt.value"
                />
              </el-option-group>
              <el-option-group v-if="couponOptions(item, 'HQ').length" label="总部券模板">
                <el-option
                  v-for="opt in couponOptions(item, 'HQ')"
                  :key="`b-hq-${opt.value}`"
                  :label="opt.label"
                  :value="opt.value"
                />
              </el-option-group>
              <el-option
                v-for="opt in couponOptions(item, 'CURRENT')"
                :key="`b-cur-${opt.value}`"
                :label="opt.label"
                :value="opt.value"
              />
            </el-select>
            <el-select
              v-else-if="item.type === 'enum'"
              v-model="item.strValue"
              style="width: 280px"
            >
              <el-option
                v-for="c in item.choices || []"
                :key="c"
                :label="enumLabel(item.key, c)"
                :value="c"
              />
            </el-select>
            <el-input
              v-else-if="item.type === 'string' && isLongText(item)"
              v-model="item.strValue"
              type="textarea"
              :rows="3"
              :maxlength="item.max_length || 2000"
              show-word-limit
              style="max-width: 520px"
            />
            <el-input
              v-else-if="item.type === 'string'"
              v-model="item.strValue"
              :maxlength="item.max_length || 256"
              show-word-limit
              style="max-width: 420px"
              clearable
            />
            <el-input-number
              v-else-if="item.type === 'int'"
              v-model="item.numValue"
              :min="item.min"
              :max="item.max"
              :step="1"
              :controls="true"
            />
            <el-input-number
              v-else-if="item.type === 'float'"
              v-model="item.numValue"
              :min="item.min"
              :max="item.max"
              :step="item.step || 0.01"
              :precision="2"
            />
            <span v-if="item.unit" class="unit">{{ item.unit }}</span>
            <el-tag v-if="isDirty(item)" size="small" type="warning" style="margin-left:8px">已改</el-tag>
            <el-tag v-if="item.has_override" size="small" type="success" style="margin-left:8px">本店已设置</el-tag>
            <el-tag v-else size="small" style="margin-left:8px">沿用平台默认</el-tag>
            <el-button
              v-if="item.has_override"
              link
              type="danger"
              size="small"
              style="margin-left:8px"
              :loading="clearingKey === item.key"
              @click="clearStoreOverride(item)"
            >
              清除
            </el-button>
            <p class="field-desc">
              {{ item.description }}
              <template v-if="item.global_value != null">
                · 平台默认 {{ formatGlobalHint(item) }}
              </template>
              <template v-if="item.type === 'int' || item.type === 'float'">
                · 范围 {{ item.min }} ~ {{ item.max }}
              </template>
              <template v-else-if="item.type === 'enum'">
                · 可选 {{ (item.choices || []).join(' / ') }}
              </template>
            </p>
          </el-form-item>

          <el-form-item v-if="itemsOfGroup(g.id).length">
            <el-button
              type="primary"
              :loading="saving"
              :disabled="!canSaveGroup(g.id)"
              @click="saveBusinessGroup(g.id)"
            >
              {{ `保存本店「${g.label}」` }}
            </el-button>
            <el-button :disabled="!dirtyCountByGroup[g.id]" @click="resetBusinessGroup(g.id)">
              还原本页未保存修改
            </el-button>
            <span v-if="dirtyCountByGroup[g.id]" class="dirty-hint">本页 {{ dirtyCountByGroup[g.id] }} 项已改</span>
          </el-form-item>
        </el-form>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api'

const props = defineProps<{
  storeId?: number | null
  preferredGroup?: string
}>()

const emit = defineEmits<{
  (e: 'group-change', groupId: string): void
}>()

const loading = ref(false)
const saving = ref(false)
const clearingKey = ref('')
const businessForm = ref<any[]>([])
const businessGroups = ref<any[]>([])
const storeName = ref('')
const innerTab = ref('')

const storeTitle = computed(() => {
  const name = storeName.value || (props.storeId ? `门店 #${props.storeId}` : '未选门店')
  return `本店业务参数 · ${name}`
})

const storeGroups = computed(() =>
  businessGroups.value.filter((g) => itemsOfGroup(g.id).length > 0),
)

const ENUM_LABELS: Record<string, Record<string, string>> = {
  kitchen_ticket_order_no: {
    barcode: '条码+单号',
    text: '仅单号',
    off: '不打印',
  },
  printer_mode: {
    local: '局域网 ESC/POS',
    xpyun: '芯烨云',
  },
  birthday_benefit_mode: {
    off: '关闭',
    points: '仅积分倍数',
    coupon: '仅赠券',
    points_and_coupon: '积分 + 赠券',
  },
}

const COUPON_NAME_SELECT_KEYS = new Set([
  'welcome_coupon_name',
  'birthday_coupon_name',
  'sleep_wake_coupon_name_30',
  'sleep_wake_coupon_name_90',
  'love_day_soy_template_name',
])

function isCouponNameSelect(key: string) {
  return COUPON_NAME_SELECT_KEYS.has(key)
}

function enumLabel(key: string, value: string) {
  return ENUM_LABELS[key]?.[value] || value
}

function isLongText(item: any) {
  // 新人礼包虽 max_length≥500，但用多选，不当成长文本框
  if (item.key === 'welcome_bundle_template_ids') return false
  const max = Number(item.max_length || 0)
  return max >= 500
}

function itemsOfGroup(groupId: string) {
  return businessForm.value.filter((i) => i.group === groupId)
}

function couponOptions(item: any, scope: 'STORE' | 'HQ' | 'CURRENT') {
  return (item?.options || []).filter((o: any) => o.scope === scope)
}

function parseBundleIdList(raw: string, idValues?: string[]): string[] {
  if (Array.isArray(idValues) && idValues.length) {
    return idValues.map((x) => String(x))
  }
  const text = (raw || '').trim()
  if (!text) return []
  try {
    const data = JSON.parse(text)
    if (!Array.isArray(data)) return []
    return data.map((x) => String(parseInt(String(x), 10))).filter((x) => /^\d+$/.test(x) && Number(x) > 0)
  } catch {
    return []
  }
}

function onBundleIdsChange(item: any) {
  const ids = (item.idListValue || [])
    .map((x: string) => parseInt(String(x), 10))
    .filter((n: number) => Number.isFinite(n) && n > 0)
  item.strValue = ids.length ? JSON.stringify(ids) : ''
}

function isDirty(item: any) {
  if (isCouponNameSelect(item.key) || item.key === 'welcome_bundle_template_ids') {
    return (item.strValue || '') !== (item._savedStr || '')
  }
  if (item.type === 'bool') return item.boolValue !== item._savedBool
  if (item.type === 'string' || item.type === 'enum') return item.strValue !== item._savedStr
  return item.numValue !== item._saved
}

const dirtyCountByGroup = computed(() => {
  const map: Record<string, number> = {}
  for (const item of businessForm.value) {
    if (!isDirty(item)) continue
    map[item.group] = (map[item.group] || 0) + 1
  }
  return map
})

function canSaveGroup(groupId: string) {
  const items = itemsOfGroup(groupId)
  if (!items.length) return false
  if (!items.some((i) => isDirty(i))) return false
  return items.every((i) => {
    if (i.type === 'bool' || i.type === 'string' || i.type === 'enum') return true
    return Number.isFinite(i.numValue)
  })
}

function parseNum(item: any) {
  const n = item.type === 'float' ? parseFloat(item.value) : parseInt(item.value, 10)
  if (!Number.isFinite(n)) {
    return item.type === 'float' ? item.min ?? 0 : item.min ?? 0
  }
  return n
}

function parseBool(item: any) {
  if (typeof item.bool_value === 'boolean') return item.bool_value
  const s = String(item.value ?? '').trim().toLowerCase()
  return ['1', 'true', 'yes', 'on', 'y'].includes(s)
}

function formatGlobalHint(item: any) {
  const raw = item.global_value
  if (item.type === 'bool') {
    const on = ['1', 'true', 'yes', 'on', 'y'].includes(String(raw ?? '').trim().toLowerCase())
    return on ? '开' : '关'
  }
  if (item.type === 'enum') return enumLabel(item.key, String(raw ?? ''))
  return raw == null || raw === '' ? '（空）' : String(raw)
}

function buildBusinessForm(items: any[]) {
  businessForm.value = items.map((item) => {
    if (item.type === 'bool') {
      const boolValue = parseBool(item)
      return { ...item, boolValue, _savedBool: boolValue }
    }
    if (item.type === 'string' || item.type === 'enum') {
      const strValue = item.value == null ? '' : String(item.value)
      const row: any = { ...item, strValue, _savedStr: strValue }
      if (item.key === 'welcome_bundle_template_ids') {
        row.idListValue = parseBundleIdList(strValue, item.id_values)
      }
      return row
    }
    const numValue = parseNum(item)
    return { ...item, numValue, _saved: numValue }
  })
}

function pickInnerTab() {
  const groups = storeGroups.value
  if (!groups.length) {
    innerTab.value = ''
    return
  }
  const prefer = props.preferredGroup || innerTab.value
  if (prefer && groups.some((g) => g.id === prefer)) {
    innerTab.value = prefer
  } else {
    innerTab.value = groups[0].id
  }
}

async function load() {
  if (!props.storeId) {
    businessForm.value = []
    businessGroups.value = []
    storeName.value = ''
    return
  }
  loading.value = true
  try {
    const res = await api.get(`/settings/stores/${props.storeId}/business`)
    const data = res.data || {}
    storeName.value = data.store_name || ''
    buildBusinessForm(data.items || [])
    businessGroups.value = data.business_groups || []
    pickInnerTab()
  } catch (e: any) {
    businessForm.value = []
    businessGroups.value = []
    ElMessage.error(e.message || '加载门店设置失败')
  } finally {
    loading.value = false
  }
}

async function clearStoreOverride(item: any) {
  if (!props.storeId || !item?.key) return
  clearingKey.value = item.key
  try {
    await api.delete(`/settings/stores/${props.storeId}/business/${item.key}`)
    ElMessage.success(`已清除「${item.label}」，本店改回平台默认`)
    await load()
  } catch (e: any) {
    ElMessage.error(e.message || '清除失败')
  } finally {
    clearingKey.value = ''
  }
}

function resetBusinessGroup(groupId: string) {
  for (const item of itemsOfGroup(groupId)) {
    if (item.type === 'bool') item.boolValue = item._savedBool
    else if (item.type === 'string' || item.type === 'enum') {
      item.strValue = item._savedStr
      if (item.key === 'welcome_bundle_template_ids') {
        item.idListValue = parseBundleIdList(item._savedStr || '', item.id_values)
      }
    } else item.numValue = item._saved
  }
}

function payloadOfItem(item: any): string | number | boolean {
  if (item.type === 'bool') return item.boolValue
  if (isCouponNameSelect(item.key) || item.key === 'welcome_bundle_template_ids') {
    return item.strValue || ''
  }
  if (item.type === 'string' || item.type === 'enum') return item.strValue
  return item.numValue
}

async function saveBusinessGroup(groupId: string) {
  const dirty = itemsOfGroup(groupId).filter((i) => isDirty(i))
  if (!dirty.length) {
    ElMessage.info('没有需要保存的修改')
    return
  }
  for (const item of dirty) {
    if (item.type === 'bool' || item.type === 'string' || item.type === 'enum') continue
    if (!Number.isFinite(item.numValue)) {
      ElMessage.warning(`${item.label} 数值无效`)
      return
    }
    if (item.numValue < item.min || item.numValue > item.max) {
      ElMessage.warning(`${item.label} 须在 ${item.min}~${item.max}`)
      return
    }
  }
  if (!props.storeId) {
    ElMessage.warning('请选择门店')
    return
  }
  saving.value = true
  try {
    const settings: Record<string, string | number | boolean> = {}
    for (const item of dirty) {
      settings[item.key] = payloadOfItem(item)
    }
    const res = await api.put(`/settings/stores/${props.storeId}/business`, { updates: settings })
    buildBusinessForm(res.data?.items || [])
    if (res.data?.business_groups?.length) {
      businessGroups.value = res.data.business_groups
    }
    storeName.value = res.data?.store_name || storeName.value
    ElMessage.success(`已保存 ${storeName.value || '本店'} ${dirty.length} 项`)
  } catch (e: any) {
    ElMessage.error(e.message || '保存失败')
  } finally {
    saving.value = false
  }
}

function onInnerTab(name: string | number) {
  emit('group-change', String(name))
}

watch(
  () => props.storeId,
  () => {
    load()
  },
  { immediate: true },
)

watch(
  () => props.preferredGroup,
  (g) => {
    if (g && storeGroups.value.some((x) => x.id === g)) {
      innerTab.value = g
    }
  },
)

defineExpose({ reload: load })
</script>

<style scoped>
.store-biz { min-height: 240px; }
.top-alert { margin-bottom: 12px; }
.group-alert { margin-bottom: 16px; }
.tab-label { display: inline-flex; align-items: center; gap: 4px; }
.tab-badge { margin-left: 2px; }
.business-form { max-width: 780px; margin-top: 8px; }
.field-desc { margin: 4px 0 0; font-size: 12px; color: #999; }
.unit { margin-left: 8px; color: #666; font-size: 13px; }
.dirty-hint { margin-left: 12px; color: #e6a23c; font-size: 12px; }
</style>
