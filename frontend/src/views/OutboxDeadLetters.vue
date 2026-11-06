<template>
  <div v-loading="loading" class="outbox-page">
    <el-alert
      title="外部结果未知的死信不会自动重试。必须先到平台核实：确认未执行才可重放；确认已执行则关闭死信。"
      type="warning"
      :closable="false"
      show-icon
      class="notice"
    />
    <el-row :gutter="12" class="summary">
      <el-col :xs="24" :sm="8">
        <el-card shadow="never">
          <div class="summary-label">待处置死信</div>
          <div class="summary-value">{{ total }}</div>
        </el-card>
      </el-col>
      <el-col :xs="24" :sm="8">
        <el-card shadow="never">
          <div class="summary-label">当前页</div>
          <div class="summary-value">{{ rows.length }}</div>
        </el-card>
      </el-col>
      <el-col :xs="24" :sm="8">
        <el-card shadow="never">
          <div class="summary-label">权限范围</div>
          <div class="summary-value">仅超级管理员</div>
        </el-card>
      </el-col>
    </el-row>

    <div class="toolbar">
      <el-button type="primary" :loading="loading" @click="load">刷新</el-button>
    </div>

    <el-table :data="rows" border empty-text="暂无达到最大重试次数的死信">
      <el-table-column prop="id" label="事件 ID" width="90" />
      <el-table-column label="业务对象" min-width="170">
        <template #default="{ row }">{{ row.aggregate_type }} #{{ row.aggregate_id }}</template>
      </el-table-column>
      <el-table-column prop="event_kind" label="事件类型" min-width="150" show-overflow-tooltip />
      <el-table-column label="重试次数" width="110">
        <template #default="{ row }">{{ row.attempt_count }} / {{ row.max_attempts }}</template>
      </el-table-column>
      <el-table-column label="外部结果" width="110">
        <template #default="{ row }">
          <el-tag v-if="row.result_unknown" type="warning">结果未知</el-tag>
          <span v-else>普通死信</span>
        </template>
      </el-table-column>
      <el-table-column label="最后错误" min-width="260" show-overflow-tooltip>
        <template #default="{ row }">{{ row.last_error || '—' }}</template>
      </el-table-column>
      <el-table-column label="最后更新" width="180">
        <template #default="{ row }">{{ formatDate(row.updated_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="210" fixed="right">
        <template #default="{ row }">
          <template v-if="row.result_unknown">
            <el-button type="success" link :loading="resolvingId === row.id" @click="resolveAsSuccess(row)">
              核实已成功
            </el-button>
            <el-button type="warning" link :loading="replayingId === row.id" @click="replay(row)">
              核实未执行后重放
            </el-button>
          </template>
          <el-button v-else type="warning" link :loading="replayingId === row.id" @click="replay(row)">
            人工重放
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <div class="pager">
      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :total="total"
        :page-sizes="[20, 50, 100]"
        layout="total, sizes, prev, pager, next"
        @current-change="load"
        @size-change="reload"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  listOutboxDeadLetters,
  replayOutboxDeadLetter,
  resolveOutboxDeadLetter,
  type OutboxDeadLetter,
} from '@/api/outbox'

const loading = ref(false)
const rows = ref<OutboxDeadLetter[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const replayingId = ref<number | null>(null)
const resolvingId = ref<number | null>(null)

function formatDate(value: string | null) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false })
}

async function load() {
  loading.value = true
  try {
    const res = await listOutboxDeadLetters(page.value, pageSize.value)
    rows.value = res.data?.items || []
    total.value = Number(res.data?.total || 0)
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '读取 Outbox 死信失败')
  } finally {
    loading.value = false
  }
}

function reload() {
  page.value = 1
  load()
}

async function replay(row: OutboxDeadLetter) {
  try {
    const { value } = await ElMessageBox.prompt(
      row.result_unknown
        ? `事件 #${row.id} 的平台执行结果未知。请先核实对应外卖平台/打印设备确实没有执行；原因中必须包含“下游核实未执行”，否则系统拒绝重放。`
        : `将事件 #${row.id} 重新放入待处理队列。请填写故障修复情况或重放原因（至少 8 个字符）。`,
      '确认人工重放',
      {
        confirmButtonText: '确认重放',
        cancelButtonText: '取消',
        inputType: 'textarea',
        inputPlaceholder: row.result_unknown
          ? '下游核实未执行；订单仍待商家接单，现重新发起确认'
          : '例如：已修复平台授权过期，确认下游接口恢复正常',
        inputValidator: (value: string) => {
          if (value.trim().length < 8) return '原因至少需要 8 个字符'
          if (row.result_unknown && !value.includes('下游核实未执行')) {
            return '结果未知事件必须先核实平台/设备状态，并在原因中写入“下游核实未执行”'
          }
          return true
        },
      },
    )
    replayingId.value = row.id
    await replayOutboxDeadLetter(row.id, value.trim(), Boolean(row.result_unknown))
    ElMessage.success(`事件 #${row.id} 已重新排队`)
    await load()
  } catch (error: any) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(error?.response?.data?.detail || error?.message || '重放失败')
  } finally {
    replayingId.value = null
  }
}

async function resolveAsSuccess(row: OutboxDeadLetter) {
  try {
    const { value } = await ElMessageBox.prompt(
      `请确认已核实对应外卖平台/打印设备已成功执行。关闭后系统不会再次发送该操作。`,
      '核实外部操作已成功',
      {
        confirmButtonText: '确认关闭死信',
        cancelButtonText: '取消',
        inputType: 'textarea',
        inputPlaceholder: '例如：已在美团商家端确认订单接单成功，平台订单状态为已确认',
        inputValidator: (value: string) => value.trim().length >= 8 || '核实说明至少需要 8 个字符',
      },
    )
    resolvingId.value = row.id
    await resolveOutboxDeadLetter(row.id, value.trim())
    ElMessage.success(`事件 #${row.id} 已按外部平台成功结果关闭`)
    await load()
  } catch (error: any) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(error?.response?.data?.detail || error?.message || '关闭死信失败')
  } finally {
    resolvingId.value = null
  }
}

onMounted(load)
</script>

<style scoped>
.outbox-page { display: flex; flex-direction: column; gap: 14px; }
.notice { margin-bottom: 2px; }
.summary-label { color: var(--el-text-color-secondary); font-size: 13px; }
.summary-value { margin-top: 8px; font-size: 24px; font-weight: 700; }
.toolbar { display: flex; justify-content: flex-end; }
.pager { display: flex; justify-content: flex-end; margin-top: 12px; }
</style>
