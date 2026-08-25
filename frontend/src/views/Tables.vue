<template>
  <el-button type="primary" @click="addTable">新增桌台</el-button>
  <el-table :data="tables" style="margin-top:16px">
    <el-table-column prop="table_no" label="桌号" />
    <el-table-column prop="table_name" label="名称" />
    <el-table-column prop="qr_token" label="Token" show-overflow-tooltip />
    <el-table-column label="微信二维码"><template #default="{row}"><a :href="row.wechat_qr_url" target="_blank">链接</a></template></el-table-column>
    <el-table-column label="操作"><template #default="{row}"><el-button link @click="regenerate(row.id)">重新生成</el-button></template></el-table-column>
  </el-table>
</template>
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import api from '@/api'
const tables = ref<any[]>([])
async function load() { tables.value = (await api.get('/tables')).data.items }
onMounted(load)
async function addTable() {
  const no = prompt('桌号') || 'A01'
  await api.post('/tables', null, { params: { table_no: no, table_name: no } })
  load()
}
async function regenerate(id: number) { await api.post(`/tables/${id}/regenerate-qr`); load() }
</script>
