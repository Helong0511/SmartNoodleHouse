<template><el-table :data="categories"><el-table-column prop="name" label="分类名" /><el-table-column prop="sort_order" label="排序" /></el-table></template>
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import api from '@/api'
const categories = ref<any[]>([])
onMounted(async () => { categories.value = (await api.get('/categories')).data })
</script>
