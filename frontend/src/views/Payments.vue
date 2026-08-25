<template><el-table :data="payments"><el-table-column prop="payment_no" label="支付号" /><el-table-column prop="provider" label="渠道" /><el-table-column prop="amount" label="金额" /><el-table-column prop="status" label="状态" /></el-table></template>
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import api from '@/api'
const payments = ref<any[]>([])
onMounted(async () => { payments.value = (await api.get('/payments')).data.items })
</script>
