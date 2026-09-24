<template>
  <div class="redirect-hint">正在跳转到「门店信息」…</div>
</template>

<script setup lang="ts">
import { onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'

const route = useRoute()
const router = useRouter()

onMounted(() => {
  const storeId = route.query.store_id
  router.replace({
    path: '/store-info',
    query: {
      tab: 'delivery',
      ...(storeId ? { store_id: String(storeId) } : {}),
    },
  })
})
</script>

<style scoped>
.redirect-hint {
  padding: 24px;
  color: #909399;
}
</style>
