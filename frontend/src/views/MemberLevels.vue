<template>
  <div v-loading="loading" class="member-levels-page">
    <div class="page-header">
      <div>
        <h2>会员等级</h2>
        <p class="hint">{{ pointsRule }}。可调整银卡、金卡的升级积分与折扣；保存后按新门槛重算全部顾客等级。</p>
      </div>
      <div class="header-actions">
        <el-button @click="load">刷新</el-button>
        <el-button v-if="canManage" type="primary" :loading="saving" :disabled="!dirty" @click="save">
          保存
        </el-button>
      </div>
    </div>

    <el-alert
      type="info"
      show-icon
      :closable="false"
      class="tip"
      title="规则说明"
      description="普通会员为起始档，升级积分为 0 且不可改。银卡、金卡须填写更高积分门槛，且金卡门槛必须高于银卡。折扣率 1 表示无折扣，0.98 表示 9.8 折。"
    />

    <el-table :data="rows" border style="margin-top:16px">
      <el-table-column prop="name" label="等级" width="140" />
      <el-table-column label="升级所需积分" min-width="180">
        <template #default="{ row }">
          <el-input-number
            v-if="canManage && row.editable_min_points"
            v-model="row.min_points"
            :min="1"
            :max="10000000"
            :step="10"
            controls-position="right"
            style="width: 160px"
          />
          <span v-else>{{ row.min_points }}</span>
        </template>
      </el-table-column>
      <el-table-column label="折扣率" min-width="200">
        <template #default="{ row }">
          <el-input-number
            v-if="canManage"
            v-model="row.discount_rate"
            :min="0.0001"
            :max="1"
            :step="0.01"
            :precision="4"
            controls-position="right"
            style="width: 160px"
          />
          <span v-else>{{ row.discount_rate }}</span>
        </template>
      </el-table-column>
      <el-table-column label="权益" width="120">
        <template #default="{ row }">
          {{ discountText(row.discount_rate) }}
        </template>
      </el-table-column>
      <el-table-column prop="member_count" label="当前人数" width="110" />
    </el-table>

    <p v-if="!rows.length" class="muted">暂无会员等级数据，请先执行数据库种子。</p>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api'
import { useAuthStore } from '@/stores/auth'

type LevelRow = {
  id: number
  name: string
  min_points: number
  discount_rate: number
  discount_text?: string | null
  is_base: boolean
  editable_min_points: boolean
  member_count: number
  _savedMin: number
  _savedRate: number
}

const auth = useAuthStore()
const canManage = computed(() => auth.hasPermission('customer:manage'))
const loading = ref(false)
const saving = ref(false)
const rows = ref<LevelRow[]>([])
const pointsRule = ref('实付1元=1积分，积分达标自动升级享对应折扣')

const dirty = computed(() =>
  rows.value.some((r) => r.min_points !== r._savedMin || r.discount_rate !== r._savedRate),
)

function discountText(rate: number) {
  if (rate == null || Number(rate) >= 1) return '无折扣'
  return `${(Number(rate) * 10).toFixed(1)}折`
}

function mapRows(items: any[]): LevelRow[] {
  return (items || []).map((it) => {
    const minPts = Number(it.min_points) || 0
    const rate = Number(it.discount_rate)
    return {
      id: it.id,
      name: it.name,
      min_points: minPts,
      discount_rate: Number.isFinite(rate) ? rate : 1,
      discount_text: it.discount_text,
      is_base: !!it.is_base,
      editable_min_points: !!it.editable_min_points,
      member_count: Number(it.member_count) || 0,
      _savedMin: minPts,
      _savedRate: Number.isFinite(rate) ? rate : 1,
    }
  })
}

async function load() {
  loading.value = true
  try {
    const res = await api.get('/member-levels')
    pointsRule.value = res.data?.points_rule || pointsRule.value
    rows.value = mapRows(res.data?.items || [])
  } catch (e: any) {
    ElMessage.error(e.response?.data?.message || e.message || '加载会员等级失败')
  } finally {
    loading.value = false
  }
}

async function save() {
  if (!rows.value.length) return
  const silver = rows.value.find((r) => r.name === '银卡会员')
  const gold = rows.value.find((r) => r.name === '金卡会员')
  if (silver && gold && gold.min_points <= silver.min_points) {
    ElMessage.warning('金卡升级积分必须高于银卡')
    return
  }
  saving.value = true
  try {
    const res = await api.put('/member-levels', {
      items: rows.value.map((r) => ({
        id: r.id,
        min_points: r.min_points,
        discount_rate: r.discount_rate,
      })),
      recalculate: true,
    })
    rows.value = mapRows(res.data?.items || [])
    ElMessage.success(res.message || '已保存')
  } catch (e: any) {
    ElMessage.error(e.response?.data?.message || e.message || '保存失败')
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  margin-bottom: 12px;
}
.page-header h2 {
  margin: 0 0 6px;
  font-size: 20px;
}
.hint {
  margin: 0;
  color: #666;
  font-size: 13px;
  max-width: 640px;
}
.header-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}
.tip {
  margin-bottom: 8px;
}
.muted {
  color: #999;
  margin-top: 16px;
}
</style>
