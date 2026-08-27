<template>
  <div class="mobile-order">
    <header>
      <h1>智能面馆 · 扫码点餐</h1>
      <p v-if="table">桌号: {{ table.table_name }}</p>
    </header>

    <!-- 扫码后会员/人脸引导 -->
    <div v-if="showMemberPrompt" class="prompt-mask">
      <div class="prompt-card">
        <h2>注册会员，享受更多便利</h2>
        <p class="desc">{{ memberText }}</p>
        <el-input v-model="nickname" placeholder="怎么称呼您（选填）" style="margin-bottom:12px" />
        <el-checkbox v-model="enableFace">同时开启人脸迎宾（主动授权，可随时关闭）</el-checkbox>
        <p v-if="enableFace" class="face-tip">{{ faceText }}</p>
        <el-button type="primary" style="width:100%;margin-top:16px" :loading="registering" @click="registerMember">
          注册并开始点餐
        </el-button>
        <el-button style="width:100%;margin-top:8px" @click="skipMember">暂不注册，先点餐</el-button>
      </div>
    </div>

    <div v-for="cat in categories" :key="cat.id" class="section">
      <h3>{{ cat.name }}</h3>
      <div v-for="p in productsByCategory(cat.id)" :key="p.id" class="product" @click="openProduct(p)">
        <div class="info"><strong>{{ p.name }}</strong><p>{{ p.description }}</p></div>
        <div class="price">¥{{ p.price }}</div>
      </div>
    </div>
    <div class="cart-bar" @click="$router.push('/order/cart')">购物车 ({{ cartCount }}) ¥{{ cartTotal }}</div>
    <el-drawer v-model="showProduct" title="选择规格" direction="btt" size="70%">
      <div v-if="currentProduct">
        <h3>{{ currentProduct.name }} ¥{{ currentProduct.price }}</h3>
        <div v-for="g in currentProduct.option_groups" :key="g.id" class="option-group">
          <h4>{{ g.name }}</h4>
          <el-radio-group v-if="g.selection_type==='SINGLE'" v-model="selectedOptions[g.id]">
            <el-radio v-for="o in g.options" :key="o.id" :value="o.id">{{ o.name }} <span v-if="+o.extra_price">+¥{{ o.extra_price }}</span></el-radio>
          </el-radio-group>
          <el-checkbox-group v-else v-model="multiOptions[g.id]">
            <el-checkbox v-for="o in g.options" :key="o.id" :value="o.id">{{ o.name }} <span v-if="+o.extra_price">+¥{{ o.extra_price }}</span></el-checkbox>
          </el-checkbox-group>
        </div>
        <el-button type="primary" style="width:100%;margin-top:16px" @click="addToCart">加入购物车</el-button>
      </div>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '@/api'

const route = useRoute()
const table = ref<any>(null)
const categories = ref<any[]>([])
const products = ref<any[]>([])
const showProduct = ref(false)
const currentProduct = ref<any>(null)
const selectedOptions = reactive<Record<number, number>>({})
const multiOptions = reactive<Record<number, number[]>>({})
const cart = ref<any[]>(JSON.parse(localStorage.getItem('snh_cart') || localStorage.getItem('cart') || '[]'))

const showMemberPrompt = ref(false)
const nickname = ref('')
const enableFace = ref(false)
const registering = ref(false)
const customerId = ref<number | null>(null)
const customerToken = ref<string | null>(null)
const memberText = ref('注册会员后可查看历史订单、口味偏好，并支持一键复购。')
const faceText = ref('开启人脸迎宾后，到店可自动欢迎并推荐您常点口味。仅在您主动授权后生效，可随时关闭。')

const cartCount = computed(() => cart.value.reduce((s, i) => s + i.quantity, 0))
const cartTotal = computed(() => cart.value.reduce((s, i) => s + parseFloat(i.subtotal), 0).toFixed(2))

function productsByCategory(catId: number) {
  return products.value.filter(p => p.category_id === catId)
}

async function openProduct(p: any) {
  const res = await api.get(`/products/${p.id}`)
  currentProduct.value = res.data
  showProduct.value = true
}

function addToCart() {
  const optionIds: number[] = []
  for (const g of currentProduct.value.option_groups) {
    if (g.selection_type === 'SINGLE' && selectedOptions[g.id]) optionIds.push(selectedOptions[g.id])
    if (g.selection_type === 'MULTIPLE' && multiOptions[g.id]) optionIds.push(...multiOptions[g.id])
  }
  // 必选规格校验（与后端一致）
  for (const g of currentProduct.value.option_groups || []) {
    if (!g.is_required) continue
    const has =
      (g.selection_type === 'SINGLE' && selectedOptions[g.id]) ||
      (g.selection_type === 'MULTIPLE' && (multiOptions[g.id] || []).length)
    if (!has) {
      ElMessage.warning(`请选择：${g.name}`)
      return
    }
  }
  const stock = Number(currentProduct.value.stock ?? 999)
  if (stock < 999 && stock < 1) {
    ElMessage.error('库存不足')
    return
  }
  cart.value.push({
    product_id: currentProduct.value.id,
    name: currentProduct.value.name,
    quantity: 1,
    option_ids: optionIds,
    price: currentProduct.value.price,
    subtotal: currentProduct.value.price,
  })
  localStorage.setItem('snh_cart', JSON.stringify(cart.value))
  localStorage.setItem('cart', JSON.stringify(cart.value))
  showProduct.value = false
}

function mockFeature(seed: string) {
  const arr: number[] = []
  let x = 0
  for (let i = 0; i < seed.length; i++) x = (x + seed.charCodeAt(i) * (i + 1)) % 997
  for (let i = 0; i < 128; i++) {
    x = (x * 9301 + 49297) % 233280
    arr.push((x / 233280) * 2 - 1)
  }
  return arr
}

async function registerMember() {
  if (!customerId.value || !customerToken.value) return
  registering.value = true
  try {
    await api.post('/miniprogram/member/register', {
      nickname: nickname.value || '面馆会员',
      enable_face: enableFace.value
    })
    localStorage.setItem('member_prompt_done', '1')
    showMemberPrompt.value = false
    ElMessage.success(enableFace.value ? '会员已注册，请上传人脸照片完成迎宾授权' : '会员注册成功')
  } catch (e: any) {
    ElMessage.error(e.message || '注册失败')
  } finally {
    registering.value = false
  }
}

function skipMember() {
  localStorage.setItem('member_prompt_done', '1')
  showMemberPrompt.value = false
}

onMounted(async () => {
  const token = route.query.token as string
  const channel = (route.query.channel as string) || 'wechat'
  if (token) {
    localStorage.setItem('snh_table_token', token)
    localStorage.setItem('table_token', token)
    localStorage.setItem('snh_channel', channel)
    localStorage.setItem('channel', channel)
    try {
      const entry = await api.get('/miniprogram/entry', { params: { token, channel } })
      table.value = entry.data?.table
      if (entry.data?.prompts) {
        memberText.value = entry.data.prompts.member_consent_text
        faceText.value = entry.data.prompts.face_consent_text
      }
    } catch {
      table.value = (await api.get(`/tables/resolve/${token}`)).data
    }
  }

  // H5：仅当 ALLOW_AUTH_MOCK 时后端才接受；生产应改为真实 code
  const channelCode = channel === 'alipay' ? `h5_ali_${Date.now()}` : `h5_wx_${Date.now()}`
  const loginPath = channel === 'alipay' ? '/miniprogram/alipay/login' : '/miniprogram/wechat/login'
  const loginBody = channel === 'alipay'
    ? { auth_code: channelCode, table_token: token || localStorage.getItem('snh_table_token') }
    : { code: channelCode, table_token: token || localStorage.getItem('snh_table_token') }
  try {
    const res = await api.post(loginPath, loginBody, { headers: { 'X-Client': 'customer' } })
    customerId.value = res.data.customer_id
    customerToken.value = res.data.customer_token
    localStorage.setItem('customer_id', String(customerId.value))
    localStorage.setItem('snh_customer_token', customerToken.value || '')
    localStorage.setItem('customer_token', customerToken.value || '')
  } catch (e: any) {
    ElMessage.warning(e.message || '顾客登录失败（生产需真实小程序 code）')
  }

  if (!localStorage.getItem('member_prompt_done')) {
    showMemberPrompt.value = true
  }

  categories.value = (await api.get('/categories')).data
  products.value = (await api.get('/products', { params: { page_size: 100 } })).data.items
})
</script>

<style scoped>
.mobile-order { max-width: 480px; margin: 0 auto; padding-bottom: 60px; position: relative; }
header { background: linear-gradient(135deg, #ff6b35, #f7b733); color: #fff; padding: 16px; text-align: center; }
.section { padding: 12px; }
.product { display: flex; justify-content: space-between; padding: 12px; border-bottom: 1px solid #eee; cursor: pointer; }
.price { color: #ff6b35; font-weight: bold; font-size: 18px; }
.cart-bar { position: fixed; bottom: 0; left: 0; right: 0; max-width: 480px; margin: 0 auto; background: #333; color: #fff; padding: 16px; text-align: center; cursor: pointer; }
.option-group { margin: 12px 0; }
.prompt-mask {
  position: fixed; inset: 0; background: rgba(0,0,0,.45); z-index: 100;
  display: flex; align-items: center; justify-content: center; padding: 16px;
}
.prompt-card {
  background: #fff; border-radius: 16px; padding: 20px; width: 100%; max-width: 400px;
}
.prompt-card h2 { font-size: 18px; margin-bottom: 8px; }
.desc { color: #666; font-size: 14px; line-height: 1.5; margin-bottom: 12px; }
.face-tip { color: #999; font-size: 12px; margin-top: 8px; line-height: 1.5; }
</style>
