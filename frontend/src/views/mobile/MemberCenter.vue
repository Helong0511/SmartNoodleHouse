<template>
  <div class="member">
    <h2>会员中心</h2>
    <el-switch v-model="faceEnabled" active-text="人脸迎宾（需主动授权）" @change="toggleFace" />
    <p v-if="faceEnabled" class="tip">授权后仅在您到店时进行匹配欢迎，可随时撤销。</p>
    <div v-if="recommendations.length"><h3>根据您之前的口味</h3><el-tag v-for="r in recommendations" :key="r.option_code" style="margin:4px">{{ r.category }}: {{ r.option_code }}</el-tag><el-button type="primary" style="margin-top:12px" @click="reorder">一键复购</el-button></div>
  </div>
</template>
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import api from '@/api'

const faceEnabled = ref(false)
const recommendations = ref<any[]>([])
const customerId = ref<number | null>(null)
const router = useRouter()

async function toggleFace(val: boolean) {
  if (!customerId.value) return
  if (val) {
    await api.post('/miniprogram/face/consent-base64', { image_base64: btoa('h5_face_placeholder') })
  } else {
    await api.post('/miniprogram/face/revoke', {})
  }
}

async function reorder() {
  router.push('/order')
}

onMounted(async () => {
  const code = localStorage.getItem('h5_login_code') || `h5_member_${Date.now()}`
  localStorage.setItem('h5_login_code', code)
  const res = await api.post('/miniprogram/wechat/login', { code })
  customerId.value = res.data.customer_id
  localStorage.setItem('customer_token', res.data.customer_token)
  const prefs = await api.get(`/customers/${customerId.value}/preferences`)
  recommendations.value = prefs.data.preferences || []
})
</script>
<style scoped>.member { max-width:480px; margin:0 auto; padding:16px; } .tip { color:#999; font-size:13px; margin-top:8px; }</style>
