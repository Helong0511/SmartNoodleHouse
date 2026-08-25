<template><el-table :data="cameras"><el-table-column prop="name" label="名称" /><el-table-column prop="camera_type" label="类型" /><el-table-column prop="status" label="状态" /><el-table-column label="视频流"><template #default="{row}"><a :href="`/api/v1/cameras/${row.id}/stream`" target="_blank">查看</a></template></el-table-column></el-table></template>
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import api from '@/api'
const cameras = ref<any[]>([])
onMounted(async () => { cameras.value = (await api.get('/cameras')).data })
</script>
