<template>
  <router-view />
  <div v-if="chunkLoadFailed" class="chunk-recovery" role="alert" aria-live="assertive">
    <section class="chunk-recovery__panel">
      <h1>页面资源加载失败</h1>
      <p>网络可能中断，或页面版本已更新。请刷新页面重试；如果问题持续，请联系门店管理员。</p>
      <button type="button" @click="reloadPage">刷新页面</button>
    </section>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'

const chunkLoadFailed = ref(false)
const showChunkLoadError = () => {
  chunkLoadFailed.value = true
}
const reloadPage = () => window.location.reload()

onMounted(() => {
  window.addEventListener('snh:chunk-load-error', showChunkLoadError)
  if (window.sessionStorage.getItem('snh_chunk_load_failed_after_retry') === '1') {
    window.sessionStorage.removeItem('snh_chunk_load_failed_after_retry')
    showChunkLoadError()
  }
})
onUnmounted(() => window.removeEventListener('snh:chunk-load-error', showChunkLoadError))
</script>

<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
  background: #f5f7fa;
  min-height: 100vh;
}
.chunk-recovery {
  position: fixed;
  z-index: 99999;
  inset: 0;
  display: grid;
  place-items: center;
  padding: 24px;
  background: rgba(3, 10, 20, .88);
}
.chunk-recovery__panel {
  width: min(100%, 480px);
  padding: 32px;
  color: #e8f4ff;
  text-align: center;
  border: 1px solid rgba(127, 200, 255, .35);
  border-radius: 14px;
  background: #102442;
}
.chunk-recovery__panel h1 { margin-bottom: 12px; font-size: 22px; }
.chunk-recovery__panel p { color: #bfd0e2; line-height: 1.7; }
.chunk-recovery__panel button {
  margin-top: 22px;
  padding: 10px 24px;
  border: 0;
  border-radius: 8px;
  color: #08203c;
  background: #7fc8ff;
  font: inherit;
  font-weight: 700;
  cursor: pointer;
}
.chunk-recovery__panel button:focus-visible { outline: 3px solid white; outline-offset: 3px; }
</style>
