<template><el-table :data="customers"><el-table-column prop="customer_no" label="编号" /><el-table-column prop="display_name" label="昵称" /><el-table-column prop="phone" label="手机" /><el-table-column prop="order_count" label="订单数" /></el-table></template>
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import api from '@/api'
const customers = ref<any[]>([])
onMounted(async () => { customers.value = (await api.get('/customers')).data.items })
</script>
