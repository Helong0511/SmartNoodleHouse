<template>

  <div class="kitchen">

    <h2>厨房 KDS</h2>

    <div class="orders-grid">

      <el-card v-for="o in orders" :key="o.id" class="order-card" :class="[o.status, { delivery: o.is_delivery }]">

        <div class="order-header">

          <span class="table">

            <template v-if="o.is_delivery">

              {{ o.channel_label }} #{{ o.delivery?.day_seq || '-' }}

            </template>

            <template v-else>桌 {{ o.table_id || '-' }}</template>

          </span>

          <span class="meta">

            <el-tag size="small" :type="o.is_delivery ? 'danger' : 'info'">{{ o.channel_label || '堂食' }}</el-tag>

            {{ o.order_no }}

          </span>

        </div>

        <div v-if="o.is_delivery && o.delivery" class="delivery-info">
          <div>{{ o.delivery.address }}</div>
          <div>{{ o.delivery.phone }}</div>
          <div v-if="o.delivery.rider_name">骑手：{{ o.delivery.rider_name }} {{ o.delivery.rider_phone }}</div>
          <div v-if="o.delivery.logistics_status">配送：{{ o.delivery.logistics_status }}</div>
        </div>
        <div v-if="o.delivery?.urge_count > 0" class="alert urge">催单 ×{{ o.delivery.urge_count }}</div>
        <div v-if="o.delivery?.refund_status === 'PENDING'" class="alert refund">退款申请中</div>
        <div v-for="item in o.items" :key="item.product_name" class="item">

          <strong>{{ item.product_name }} ×{{ item.quantity }}</strong>

          <div class="opts">{{ item.options?.join(' / ') }}</div>

        </div>

        <div class="actions">

          <el-button v-if="o.status==='PAID' || o.status==='CONFIRMED'" type="warning" @click="updateStatus(o.id,'PREPARING')">开始制作</el-button>

          <el-button v-if="o.status==='PREPARING'" type="success" @click="updateStatus(o.id,'READY')">制作完成</el-button>

          <el-button v-if="o.status==='READY'" type="primary" @click="updateStatus(o.id,'COMPLETED')">已出餐</el-button>

        </div>

      </el-card>

    </div>

  </div>

</template>



<script setup lang="ts">

import { onMounted, onUnmounted, ref } from 'vue'

import api from '@/api'



const orders = ref<any[]>([])

let ws: WebSocket | null = null



async function load() {

  orders.value = (await api.get('/orders/kitchen/active')).data

}



async function updateStatus(id: number, status: string) {

  await api.patch(`/orders/${id}/status`, { status })

  load()

}



onMounted(() => {

  load()

  const proto = location.protocol === 'https:' ? 'wss' : 'ws'

  const token = localStorage.getItem('snh_staff_token') || localStorage.getItem('token') || ''

  ws = new WebSocket(`${proto}://${location.host}/api/v1/ws/kitchen?token=${encodeURIComponent(token)}`)

  ws.onmessage = () => load()

})



onUnmounted(() => ws?.close())

</script>



<style scoped>

.kitchen { padding: 16px; background: #1a1a2e; min-height: 100vh; color: #fff; }

.orders-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 16px; margin-top: 16px; }

.order-card { background: #16213e; border: none; color: #fff; }

.order-card.PAID, .order-card.CONFIRMED { border-left: 4px solid #e6a23c; }

.order-card.PREPARING { border-left: 4px solid #409eff; }

.order-card.READY { border-left: 4px solid #67c23a; }

.order-card.delivery { background: #1b2838; }

.order-header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 12px; gap: 8px; }

.table { font-size: 20px; font-weight: bold; }

.meta { display: flex; flex-direction: column; align-items: flex-end; gap: 4px; font-size: 12px; color: #aaa; }

.delivery-info { font-size: 13px; color: #f5a623; margin-bottom: 8px; line-height: 1.4; }
.alert { padding: 4px 8px; border-radius: 4px; font-size: 12px; margin-bottom: 8px; font-weight: bold; }
.alert.urge { background: #e6a23c; color: #1a1a2e; }
.alert.refund { background: #f56c6c; color: #fff; }
.item { margin: 8px 0; }

.opts { color: #aaa; font-size: 13px; }

.actions { margin-top: 12px; }

</style>

