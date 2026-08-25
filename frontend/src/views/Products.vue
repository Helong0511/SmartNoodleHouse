<template>
  <div>
    <el-button type="primary" @click="showAdd = true">新增商品</el-button>
    <el-table :data="products" style="margin-top:16px">
      <el-table-column prop="product_no" label="编号" width="100" />
      <el-table-column prop="name" label="名称" />
      <el-table-column prop="price" label="价格" width="100" />
      <el-table-column prop="status" label="状态" width="100" />
      <el-table-column prop="sales_count" label="销量" width="80" />
    </el-table>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import api from '@/api'

const products = ref<any[]>([])
const showAdd = ref(false)

onMounted(async () => {
  const res = await api.get('/products', { params: { page_size: 100 } })
  products.value = res.data.items
})
</script>
