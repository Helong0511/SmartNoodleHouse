<template>
    <div v-loading="loading">
        <el-form inline class="toolbar">
            <el-form-item v-if="auth.isSuperAdmin">
                <el-select v-model="storeFilter" placeholder="全部门店" clearable style="width:160px" @change="reload">
                    <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
                </el-select>
            </el-form-item>
            <el-tag v-else-if="boundStoreName" type="info" style="margin-right:12px">门店：{{ boundStoreName }}</el-tag>
            <el-form-item>
                <el-select v-model="statusFilter" placeholder="状态" clearable style="width:130px" @change="reload">
                    <el-option v-for="(lbl, key) in STATUS_LABELS" :key="key" :label="lbl" :value="key" />
                </el-select>
            </el-form-item>
            <el-button type="primary" @click="reload">查询</el-button>
            <el-button @click="resetFilters">重置</el-button>
        </el-form>

        <el-table :data="items" border>
            <el-table-column prop="created_at" label="申请时间" width="170" />
            <el-table-column prop="order_no" label="订单号" min-width="220" show-overflow-tooltip />
            <el-table-column label="门店" min-width="110" show-overflow-tooltip>
                <template #default="{ row }">{{ row.store_name || (row.store_id ? `门店#${row.store_id}` : '—')
                }}</template>
            </el-table-column>
            <el-table-column prop="title" label="发票抬头" min-width="180" show-overflow-tooltip />
            <el-table-column label="类型" min-width="200" show-overflow-tooltip>
                <template #default="{ row }">
                    <template v-if="row.title_type === 'COMPANY'">
                        单位<span v-if="row.tax_no">（税号：{{ row.tax_no }}）</span>
                    </template>
                    <span v-else>个人</span>
                </template>
            </el-table-column>
            <el-table-column prop="email" label="邮箱" min-width="170" show-overflow-tooltip />
            <el-table-column label="金额" width="110">
                <template #default="{ row }">¥{{ formatAmount(row.amount) }}</template>
            </el-table-column>
            <el-table-column label="状态" width="100">
                <template #default="{ row }">
                    <el-tag size="small" :type="STATUS_TAG_TYPES[row.status] || 'info'">
                        {{ STATUS_LABELS[row.status] || '—' }}
                    </el-tag>
                </template>
            </el-table-column>
            <el-table-column label="驳回原因" min-width="150" show-overflow-tooltip>
                <template #default="{ row }">{{ row.reject_reason || '—' }}</template>
            </el-table-column>
            <el-table-column label="操作" width="170" fixed="right">
                <template #default="{ row }">
                    <template v-if="row.status === 'PENDING'">
                        <el-button link type="success" :loading="busyId === row.id"
                            @click="markIssued(row)">标记已开票</el-button>
                        <el-button link type="danger" @click="reject(row)">驳回</el-button>
                    </template>
                    <span v-else class="muted">—</span>
                </template>
            </el-table-column>
        </el-table>

        <el-pagination v-model:current-page="page" v-model:page-size="pageSize" :total="total"
            :page-sizes="[20, 50, 100]" layout="total, sizes, prev, pager, next" style="margin-top:16px"
            @current-change="load" @size-change="reload" />
    </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api'
import { useStoreOptions } from '@/composables/useStoreOptions'
import { useAuthStore } from '@/stores/auth'

const STATUS_LABELS: Record<string, string> = {
    PENDING: '待开票',
    ISSUED: '已开票',
    REJECTED: '已驳回',
}
const STATUS_TAG_TYPES: Record<string, string> = {
    PENDING: 'warning',
    ISSUED: 'success',
    REJECTED: 'info',
}

const auth = useAuthStore()
const loading = ref(false)
const items = ref<any[]>([])
const { storeOptions: stores, loadStores } = useStoreOptions()
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const storeFilter = ref<number | undefined>()
const statusFilter = ref<string | undefined>()
const busyId = ref<number | null>(null)

const boundStoreName = computed(() => {
    const sid = auth.user?.store_id
    if (!sid) return ''
    return stores.value.find((s) => s.id === sid)?.name || `门店#${sid}`
})

function reload() {
    page.value = 1
    load()
}

function resetFilters() {
    storeFilter.value = undefined
    statusFilter.value = undefined
    reload()
}

async function load() {
    loading.value = true
    try {
        const params: Record<string, unknown> = {
            page: page.value,
            page_size: pageSize.value,
            status: statusFilter.value,
            store_id: auth.isSuperAdmin ? storeFilter.value : undefined,
        }
        const res: any = await api.get('/admin/invoices', { params })
        // 防御性解析：兼容 {code,data:{items,total}} 与直接返回 {items,total} 或数组
        const body = res?.data ?? res ?? {}
        const list = Array.isArray(body) ? body : body.items || []
        // 末页删空自动回退一页
        if (list.length === 0 && page.value > 1) {
            page.value -= 1
            loading.value = false
            await load()
            return
        }
        items.value = list
        total.value = Number(Array.isArray(body) ? list.length : body.total ?? list.length) || 0
    } catch (e: any) {
        ElMessage.error(e?.message || '加载发票申请失败')
    } finally {
        loading.value = false
    }
}

function formatAmount(v: any) {
    const n = Number(v)
    return Number.isFinite(n) ? n.toFixed(2) : '0.00'
}

async function markIssued(row: any) {
    try {
        await ElMessageBox.confirm(
            `确认为订单 ${row.order_no || `#${row.order_id}`} 标记已开票？开票结果将发送至 ${row.email || '顾客邮箱'}`,
            '标记已开票',
            { confirmButtonText: '确认开票', cancelButtonText: '取消', type: 'warning' },
        )
    } catch {
        return
    }
    busyId.value = row.id
    try {
        await api.post(`/admin/invoices/${row.id}/issue`)
        ElMessage.success('已标记开票')
        await load()
    } catch (e: any) {
        ElMessage.error(e.message || '操作失败')
    } finally {
        busyId.value = null
    }
}

async function reject(row: any) {
    let reason = ''
    try {
        const res = await ElMessageBox.prompt('请填写驳回原因（必填）', '驳回开票申请', {
            confirmButtonText: '确认驳回',
            cancelButtonText: '取消',
            inputPlaceholder: '如：订单已退款，无法开票',
            inputValidator: (v: string) => (v && v.trim() ? true : '驳回原因不能为空'),
        })
        reason = (res.value || '').trim()
    } catch {
        return
    }
    busyId.value = row.id
    try {
        await api.post(`/admin/invoices/${row.id}/reject`, { reason })
        ElMessage.success('已驳回')
        await load()
    } catch (e: any) {
        ElMessage.error(e.message || '操作失败')
    } finally {
        busyId.value = null
    }
}

onMounted(async () => {
    await loadStores()
    await load()
})
</script>

<style scoped>
.toolbar {
    margin-bottom: 12px;
}

.muted {
    color: #c0c4cc;
}
</style>
