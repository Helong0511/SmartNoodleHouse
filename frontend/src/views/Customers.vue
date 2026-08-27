<template>
  <div v-loading="loading">
    <el-form inline class="toolbar">
      <el-form-item>
        <el-input
          v-model="keyword"
          placeholder="昵称 / 手机 / 编号"
          clearable
          style="width: 200px"
          @keyup.enter="reload"
        />
      </el-form-item>
      <el-form-item>
        <el-select v-model="channel" placeholder="渠道" clearable style="width: 120px" @change="reload">
          <el-option label="微信" value="WECHAT" />
          <el-option label="支付宝" value="ALIPAY" />
        </el-select>
      </el-form-item>
      <el-button type="primary" @click="reload">搜索</el-button>
      <el-button @click="load">刷新</el-button>
    </el-form>

    <el-table :data="customers" border>
      <el-table-column prop="customer_no" label="编号" min-width="150" show-overflow-tooltip />
      <el-table-column label="昵称" min-width="100">
        <template #default="{ row }">{{ row.display_name || row.nickname || '—' }}</template>
      </el-table-column>
      <el-table-column prop="phone" label="手机" width="120">
        <template #default="{ row }">{{ row.phone || '—' }}</template>
      </el-table-column>
      <el-table-column label="渠道" width="140">
        <template #default="{ row }">
          <el-tag v-if="row.channels?.includes('WECHAT')" size="small" type="success" style="margin-right:4px">微信</el-tag>
          <el-tag v-if="row.channels?.includes('ALIPAY')" size="small" type="warning">支付宝</el-tag>
          <span v-if="!row.channels?.length" class="muted">—</span>
        </template>
      </el-table-column>
      <el-table-column label="人脸" width="70">
        <template #default="{ row }">
          <el-tag size="small" :type="row.has_face ? 'success' : 'info'">{{ row.has_face ? '已开' : '未开' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="智能口味" min-width="200">
        <template #default="{ row }">
          <template v-if="row.preferences?.length">
            <el-tag
              v-for="(p, i) in row.preferences"
              :key="i"
              size="small"
              type="danger"
              effect="plain"
              style="margin:2px"
            >{{ p.label }}</el-tag>
          </template>
          <span v-else class="muted">待累计</span>
        </template>
      </el-table-column>
      <el-table-column prop="order_count" label="订单" width="70" />
      <el-table-column prop="last_order_at" label="最近下单" width="160">
        <template #default="{ row }">{{ row.last_order_at || '—' }}</template>
      </el-table-column>
      <el-table-column label="操作" width="90" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="showDetail(row.id)">详情</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-pagination
      v-model:current-page="page"
      v-model:page-size="pageSize"
      :total="total"
      layout="total, prev, pager, next"
      style="margin-top:16px"
      @current-change="load"
    />

    <el-drawer v-model="drawer" title="顾客详情 · 智能口味档案" size="520px">
      <template v-if="detail">
        <el-descriptions :column="1" border>
          <el-descriptions-item label="编号">{{ detail.customer_no }}</el-descriptions-item>
          <el-descriptions-item label="昵称">{{ detail.display_name || detail.nickname || '—' }}</el-descriptions-item>
          <el-descriptions-item label="手机">{{ detail.phone || '—' }}</el-descriptions-item>
          <el-descriptions-item label="订单数">{{ detail.order_count }}</el-descriptions-item>
          <el-descriptions-item label="人脸迎宾">{{ detail.has_face ? '已授权（到店摄像头识别）' : '未开启' }}</el-descriptions-item>
          <el-descriptions-item label="注册时间">{{ detail.created_at || '—' }}</el-descriptions-item>
          <el-descriptions-item label="最近下单">{{ detail.last_order_at || '—' }}</el-descriptions-item>
        </el-descriptions>

        <div class="pref-block">
          <h4>智能记录的口味爱好</h4>
          <p class="muted tip">完成订单后自动累计规格偏好；人脸迎宾会播报「今天还是老样子」。</p>
          <el-alert
            v-if="detail.welcome_hint"
            :title="detail.welcome_hint"
            type="success"
            :closable="false"
            show-icon
            style="margin-bottom:12px"
          />
          <el-table v-if="detail.preferences?.length" :data="detail.preferences" size="small" border>
            <el-table-column prop="category" label="类别" width="100" />
            <el-table-column label="口味" min-width="120">
              <template #default="{ row }">{{ row.option_name || row.option_code }}</template>
            </el-table-column>
            <el-table-column label="频次" width="70">
              <template #default="{ row }">{{ row.order_count }}次</template>
            </el-table-column>
            <el-table-column label="偏好度" width="90">
              <template #default="{ row }">{{ formatScore(row.score) }}</template>
            </el-table-column>
          </el-table>
          <p v-else class="muted">暂无偏好（需顾客完成至少 1 笔带规格的订单）</p>
        </div>

        <h4 style="margin-top:16px">微信账号</h4>
        <p v-if="!detail.wechat_accounts?.length" class="muted">无</p>
        <ul v-else class="acct">
          <li v-for="(w, i) in detail.wechat_accounts" :key="'w'+i">
            <code>{{ w.openid }}</code>
            <el-tag v-if="w.is_mock" size="small" type="info" style="margin-left:6px">开发 Mock</el-tag>
          </li>
        </ul>

        <h4>支付宝账号</h4>
        <p v-if="!detail.alipay_accounts?.length" class="muted">无</p>
        <ul v-else class="acct">
          <li v-for="(a, i) in detail.alipay_accounts" :key="'a'+i">
            <code>{{ a.alipay_user_id }}</code>
            <el-tag v-if="a.is_mock" size="small" type="info" style="margin-left:6px">开发 Mock</el-tag>
          </li>
        </ul>
      </template>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import api from '@/api'

const loading = ref(false)
const customers = ref<any[]>([])
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const keyword = ref('')
const channel = ref<string | undefined>()
const drawer = ref(false)
const detail = ref<any>(null)

function formatScore(s: number) {
  if (s == null) return '—'
  return `${Math.round(Number(s) * 100)}%`
}

function reload() {
  page.value = 1
  load()
}

async function load() {
  loading.value = true
  try {
    const res = await api.get('/customers', {
      params: {
        page: page.value,
        page_size: pageSize.value,
        keyword: keyword.value || undefined,
        channel: channel.value || undefined,
      },
    })
    customers.value = res.data.items
    total.value = res.data.total
  } finally {
    loading.value = false
  }
}

async function showDetail(id: number) {
  detail.value = (await api.get(`/customers/${id}`)).data
  drawer.value = true
}

onMounted(load)
</script>

<style scoped>
.toolbar { margin-bottom: 12px; }
.muted { color: #999; }
.tip { font-size: 13px; margin: 4px 0 10px; }
.pref-block { margin-top: 18px; }
.pref-block h4 { margin: 0 0 4px; }
.acct { padding-left: 18px; word-break: break-all; }
.acct code { font-size: 12px; }
</style>
