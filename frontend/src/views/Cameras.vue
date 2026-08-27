<template>
  <div>
    <el-table :data="cameras">
      <el-table-column prop="name" label="名称" />
      <el-table-column prop="camera_type" label="类型" />
      <el-table-column prop="status" label="状态" />
      <el-table-column prop="store_id" label="门店" width="80" />
      <el-table-column label="操作" width="220">
        <template #default="{ row }">
          <el-button size="small" @click="openStream(row.id)">查看</el-button>
          <el-button size="small" @click="startCam(row.id)">启动</el-button>
          <el-button size="small" @click="stopCam(row.id)">停止</el-button>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '@/api'

const cameras = ref<any[]>([])

onMounted(async () => {
  cameras.value = (await api.get('/cameras')).data
})

function openStream(id: number) {
  // 短期 stream ticket，避免把长生命周期 JWT 放进 URL
  api
    .post(`/cameras/${id}/stream-ticket`)
    .then((res) => {
      const ticket = res.data?.ticket
      if (!ticket) throw new Error('无法签发预览票据')
      window.open(`/api/v1/cameras/${id}/stream?ticket=${encodeURIComponent(ticket)}`, '_blank')
    })
    .catch((e) => ElMessage.error(e.message || '打开失败'))
}

async function startCam(id: number) {
  await api.post(`/cameras/${id}/start`)
  cameras.value = (await api.get('/cameras')).data
}

async function stopCam(id: number) {
  await api.post(`/cameras/${id}/stop`)
  cameras.value = (await api.get('/cameras')).data
}
</script>
