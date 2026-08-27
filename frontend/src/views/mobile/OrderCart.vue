<template>
  <div class="cart-page">
    <h2>购物车</h2>
    <div v-if="!cart.length" class="empty">购物车为空</div>
    <div v-for="(item, idx) in cart" :key="idx" class="item">
      <div>
        <div>{{ item.name }} ×{{ item.quantity }}</div>
        <div v-if="item._stockError" class="err">{{ item._stockError }}</div>
      </div>
      <span>¥{{ item.subtotal }}</span>
    </div>
    <el-button type="primary" style="width:100%;margin-top:24px" :disabled="!canCheckout" @click="goConfirm">
      去结算
    </el-button>
  </div>
</template>
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '@/api'

const router = useRouter()
const cart = ref<any[]>(JSON.parse(localStorage.getItem('snh_cart') || localStorage.getItem('cart') || '[]'))

const canCheckout = computed(() => cart.value.length > 0 && cart.value.every((i) => !i._stockError))

async function validateStock() {
  for (const item of cart.value) {
    item._stockError = ''
    try {
      const res = await api.get(`/products/${item.product_id}`)
      const p = res.data
      if (!p || p.status !== 'ON_SALE') {
        item._stockError = '商品已下架'
        continue
      }
      const stock = Number(p.stock ?? 999)
      if (stock < 999 && stock < (item.quantity || 1)) {
        item._stockError = `库存不足（剩余 ${stock}）`
      }
      // 服务端价格为准提示
      if (p.price != null && String(p.price) !== String(item.price) && item.price != null) {
        item.subtotal = (Number(p.price) * (item.quantity || 1)).toFixed(2)
      }
    } catch {
      item._stockError = '无法校验库存'
    }
  }
  localStorage.setItem('snh_cart', JSON.stringify(cart.value.map(({ _stockError, ...rest }) => rest)))
  localStorage.setItem('cart', localStorage.getItem('snh_cart') || '[]')
}

function goConfirm() {
  if (!canCheckout.value) {
    ElMessage.warning('请先处理库存问题')
    return
  }
  router.push('/order/confirm')
}

onMounted(validateStock)
</script>
<style scoped>
.cart-page { max-width:480px; margin:0 auto; padding:16px; }
.item { display:flex; justify-content:space-between; padding:12px 0; border-bottom:1px solid #eee; }
.err { color:#c45656; font-size:12px; margin-top:4px; }
.empty { color:#999; padding:24px 0; text-align:center; }
</style>
