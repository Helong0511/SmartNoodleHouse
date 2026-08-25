<template>
  <div class="cart-page">
    <h2>购物车</h2>
    <div v-for="(item, idx) in cart" :key="idx" class="item">
      <span>{{ item.name }} ×{{ item.quantity }}</span>
      <span>¥{{ item.subtotal }}</span>
    </div>
    <el-button type="primary" style="width:100%;margin-top:24px" @click="$router.push('/order/confirm')">去结算</el-button>
  </div>
</template>
<script setup lang="ts">
import { ref } from 'vue'
const cart = ref(JSON.parse(localStorage.getItem('cart') || '[]'))
</script>
<style scoped>.cart-page { max-width:480px; margin:0 auto; padding:16px; }</style>
