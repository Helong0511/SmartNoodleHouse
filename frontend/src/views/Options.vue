<template><el-table :data="groups"><el-table-column prop="name" label="规格组" /><el-table-column label="选项"><template #default="{row}">{{ row.options?.map((o:any)=>o.name).join(', ') }}</template></el-table-column></el-table></template>
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import api from '@/api'
const groups = ref<any[]>([])
onMounted(async () => { groups.value = (await api.get('/options/groups')).data })
</script>
