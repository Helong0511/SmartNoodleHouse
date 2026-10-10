<template>
    <div v-loading="loading">
        <el-form inline class="toolbar">
            <el-form-item v-if="auth.isSuperAdmin">
                <el-select v-model="storeId" style="width:160px" @change="load">
                    <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
                </el-select>
            </el-form-item>
            <el-tag v-else-if="boundStoreName" type="info" style="margin-right:12px">门店：{{ boundStoreName }}</el-tag>
            <el-form-item>
                <span class="stat">当前等待 <b>{{ waitingCount }}</b> 人</span>
                <span class="stat">今日已入座 <b>{{ seatedCount }}</b> 人</span>
            </el-form-item>
            <el-button type="primary" plain @click="load">刷新</el-button>
        </el-form>

        <el-alert v-if="auth.isSuperAdmin && !storeId" type="warning" :closable="false" show-icon title="请选择门店查看今日排队"
            style="margin-bottom:12px" />

        <el-table :data="sortedItems" border>
            <el-table-column label="号牌" width="120" align="center">
                <template #default="{ row }">
                    <span class="ticket-no">{{ row.ticket_no || row.number }}</span>
                </template>
            </el-table-column>
            <el-table-column prop="people_count" label="人数" width="80" align="center">
                <template #default="{ row }">{{ row.people_count ?? '—' }}</template>
            </el-table-column>
            <el-table-column label="状态" width="110" align="center">
                <template #default="{ row }">
                    <el-tag size="small" :type="STATUS_TAG_TYPES[row.status] || 'info'">
                        {{ STATUS_LABELS[row.status] || row.status }}
                    </el-tag>
                </template>
            </el-table-column>
            <el-table-column prop="issued_at" label="取号时间" width="170">
                <template #default="{ row }">{{ row.issued_at || '—' }}</template>
            </el-table-column>
            <el-table-column label="等待时长" width="120" align="center">
                <template #default="{ row }">{{ waitMinutes(row) }} 分钟</template>
            </el-table-column>
            <el-table-column label="操作" width="200" fixed="right">
                <template #default="{ row }">
                    <el-button v-if="row.status === 'WAITING'" type="primary" :loading="busyId === row.id"
                        @click="doAction(row, 'call')">叫号</el-button>
                    <template v-else-if="row.status === 'CALLED'">
                        <el-button type="success" :loading="busyId === row.id"
                            @click="doAction(row, 'seat')">入座</el-button>
                        <el-button type="danger" plain :loading="busyId === row.id"
                            @click="doAction(row, 'cancel')">取消</el-button>
                    </template>
                    <span v-else class="muted">—</span>
                </template>
            </el-table-column>
        </el-table>

        <div v-if="!sortedItems.length && !loading" class="empty-hint">今日暂无取号记录</div>
    </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api'
import { useStoreOptions } from '@/composables/useStoreOptions'
import { useAuthStore } from '@/stores/auth'

const STATUS_LABELS: Record<string, string> = {
    WAITING: '等待中',
    CALLED: '已叫号',
    SEATED: '已入座',
    CANCELLED: '已取消',
}
const STATUS_TAG_TYPES: Record<string, string> = {
    WAITING: 'warning',
    CALLED: 'primary',
    SEATED: 'success',
    CANCELLED: 'info',
}
/** WAITING/CALLED 在前，再按 number 正序（与后端约定一致，前端兜底排序） */
const STATUS_ORDER: Record<string, number> = {
    WAITING: 0,
    CALLED: 1,
    SEATED: 2,
    CANCELLED: 3,
}

const auth = useAuthStore()
const loading = ref(false)
const items = ref<any[]>([])
const { storeOptions: stores, loadStores: fetchStores } = useStoreOptions()
const storeId = ref<number | null>(null)
const busyId = ref<number | null>(null)
const nowTick = ref(Date.now())

let refreshTimer: ReturnType<typeof setInterval> | null = null

const boundStoreName = computed(() => {
    const sid = auth.user?.store_id
    if (!sid) return ''
    return stores.value.find((s) => s.id === sid)?.name || `门店#${sid}`
})

const sortedItems = computed(() => {
    return [...items.value].sort((a, b) => {
        const sa = STATUS_ORDER[a.status] ?? 9
        const sb = STATUS_ORDER[b.status] ?? 9
        if (sa !== sb) return sa - sb
        return (Number(a.number) || 0) - (Number(b.number) || 0)
    })
})

const waitingCount = computed(() => items.value.filter((x) => x.status === 'WAITING').length)
const seatedCount = computed(() => items.value.filter((x) => x.status === 'SEATED').length)

async function loadStores() {
    if (!(await fetchStores())) return
    if (auth.isSuperAdmin && !storeId.value && stores.value.length) {
        storeId.value = stores.value[0].id
    }
}

async function load() {
    if (auth.isSuperAdmin && !storeId.value) {
        items.value = []
        return
    }
    loading.value = true
    try {
        const params: Record<string, unknown> = {
            store_id: auth.isSuperAdmin ? storeId.value : undefined,
        }
        const res: any = await api.get('/admin/queue/today', { params })
        // 防御性解析：兼容 {code,data:{items}} / {items} / 数组
        const body = res?.data ?? res ?? {}
        items.value = Array.isArray(body) ? body : body.items || []
        nowTick.value = Date.now()
    } catch (e: any) {
        ElMessage.error(e?.message || '加载今日排队失败')
    } finally {
        loading.value = false
    }
}

/** 等待时长（分钟）：等待中实时计算；其余用对应状态时间点兜底 */
function waitMinutes(row: any) {
    const start = Date.parse(row.issued_at || '')
    if (Number.isNaN(start)) return 0
    const endMap: Record<string, string | undefined> = {
        CALLED: row.called_at,
        SEATED: row.seated_at,
        CANCELLED: row.cancelled_at,
    }
    const endStr = endMap[row.status]
    let end = endStr ? Date.parse(endStr) : nowTick.value
    if (Number.isNaN(end)) end = nowTick.value
    return Math.max(0, Math.floor((end - start) / 60000))
}

async function doAction(row: any, action: 'call' | 'seat' | 'cancel') {
    const confirmText: Record<string, string> = {
        call: `确认为号牌 ${row.ticket_no || row.number} 叫号？`,
        seat: `确认为号牌 ${row.ticket_no || row.number} 安排入座？`,
        cancel: `确定取消号牌 ${row.ticket_no || row.number}？`,
    }
    if (action === 'cancel') {
        try {
            await ElMessageBox.confirm(confirmText[action], '取消排队', {
                confirmButtonText: '确认取消',
                cancelButtonText: '返回',
                type: 'warning',
            })
        } catch {
            return
        }
    }
    busyId.value = row.id
    try {
        await api.post(`/admin/queue/${row.id}/${action}`)
        ElMessage.success(action === 'call' ? '已叫号' : action === 'seat' ? '已入座' : '已取消')
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
    // 30 秒自动刷新
    refreshTimer = setInterval(() => {
        nowTick.value = Date.now()
        load()
    }, 30000)
})

onUnmounted(() => {
    if (refreshTimer) {
        clearInterval(refreshTimer)
        refreshTimer = null
    }
})
</script>

<style scoped>
.toolbar {
    margin-bottom: 12px;
}

.stat {
    margin-right: 20px;
    color: #606266;
    font-size: 14px;
}

.stat b {
    color: #409eff;
    font-size: 18px;
    margin: 0 2px;
}

.ticket-no {
    font-size: 22px;
    font-weight: 800;
    color: #303133;
    letter-spacing: 1px;
    font-variant-numeric: tabular-nums;
}

.muted {
    color: #c0c4cc;
}

.empty-hint {
    margin-top: 24px;
    text-align: center;
    color: #909399;
}
</style>
