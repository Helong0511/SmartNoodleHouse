<template>
  <main class="not-found" role="main">
    <section class="not-found__panel" aria-labelledby="not-found-title">
      <p class="not-found__code">404</p>
      <h1 id="not-found-title">页面不存在</h1>
      <p class="not-found__message">你访问的页面可能已移动、被删除，或地址输入有误。</p>
      <nav class="not-found__actions" aria-label="页面导航">
        <RouterLink class="not-found__primary" to="/dashboard">返回管理首页</RouterLink>
        <RouterLink class="not-found__secondary" to="/scan">前往顾客点餐</RouterLink>
      </nav>
    </section>
  </main>
</template>

<script setup lang="ts">
import { RouterLink } from 'vue-router'
</script>

<style scoped>
.not-found {
  min-height: 100vh;
  display: grid;
  place-items: center;
  padding: 24px;
  color: #e8f4ff;
  background: radial-gradient(ellipse at top, #17345b 0%, #081426 58%, #050b15 100%);
}
.not-found__panel {
  width: min(100%, 520px);
  padding: 48px 36px;
  text-align: center;
  border: 1px solid rgba(127, 200, 255, .28);
  border-radius: 18px;
  background: rgba(10, 27, 52, .82);
  box-shadow: 0 24px 70px rgba(0, 0, 0, .32);
}
.not-found__code {
  margin-bottom: 8px;
  color: #7fc8ff;
  font-size: clamp(64px, 15vw, 104px);
  line-height: 1;
  font-weight: 800;
  letter-spacing: 5px;
}
.not-found h1 { margin: 12px 0; font-size: 25px; }
.not-found__message { color: #b7cbe0; line-height: 1.8; }
.not-found__actions {
  display: flex;
  justify-content: center;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 28px;
}
.not-found__actions a {
  min-height: 42px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0 18px;
  border-radius: 9px;
  text-decoration: none;
  font-weight: 600;
}
.not-found__primary { color: #08203c; background: #7fc8ff; }
.not-found__secondary { color: #d9ebfb; border: 1px solid rgba(127, 200, 255, .42); }
.not-found__actions a:focus-visible { outline: 3px solid #fff; outline-offset: 3px; }
@media (max-width: 420px) {
  .not-found__panel { padding: 36px 20px; }
}
</style>
