<template><div v-if="order"><h3>{{ order.order_no }}</h3><el-descriptions :column="2" border><el-descriptions-item label="状态">{{ order.status }}</el-descriptions-item><el-descriptions-item label="金额">¥{{ order.total_amount }}</el-descriptions-item></el-descriptions><el-table :data="order.items" style="margin-top:16px"><el-table-column prop="product_name" label="商品" /><el-table-column prop="quantity" label="数量" /><el-table-column prop="subtotal" label="小计" /></el-table></div></template>
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import api from '@/api'
const route = useRoute()
const order = ref<any>(null)
onMounted(async () => { order.value = (await api.get(`/orders/${route.params.id}`)).data })
</script>
