<template>
  <div class="mobile-order">
    <header>
      <img src="/logo.png?v=202609080938" alt="肠旺面" class="header-logo" />
      <h1>扫码点餐</h1>
      <p v-if="table">桌号: {{ table.table_name }}</p>
    </header>

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
      <div
        v-for="p in productsByCategory(cat.id)"
        :key="p.id"
        class="product"
        @click="openProduct(p)"
      >
        <div class="thumb-wrap" :style="{ width: thumbPx + 'px', height: thumbPx + 'px' }">
          <img v-if="p._imageUrl" :src="p._imageUrl" class="thumb" alt="" />
          <div v-else class="thumb placeholder">{{ (p.name || '餐')[0] }}</div>
        </div>
        <div class="info">
          <strong>{{ p.name }}</strong>
          <p v-if="p.description">{{ p.description }}</p>
          <div class="price">¥{{ p.price }}</div>
        </div>
      </div>
    </div>
    <div class="cart-bar" @click="$router.push('/order/cart')">购物车 ({{ cartCount }}) ¥{{ cartTotal }}</div>

    <el-drawer v-model="showProduct" title="选择规格" direction="btt" size="78%">
      <div v-if="currentProduct" class="drawer-body">
        <div class="hero" :style="{ height: heroHeight + 'px' }">
          <img
            v-if="currentProduct._imageUrl"
            :src="currentProduct._imageUrl"
            class="hero-img"
            alt=""
          />
          <div v-else class="hero-img placeholder">{{ (currentProduct.name || '餐')[0] }}</div>
        </div>
        <h3>{{ currentProduct.name }} ¥{{ currentProduct.price }}</h3>
        <p v-if="prefHint" class="pref-hint">{{ prefHint }}</p>
        <p v-if="currentProduct.description" class="desc">{{ currentProduct.description }}</p>
        <div v-for="g in currentProduct.option_groups" :key="g.id" class="option-group">
          <h4>{{ g.name }}</h4>
          <el-radio-group v-if="g.selection_type==='SINGLE'" v-model="selectedOptions[g.id]">
            <el-radio v-for="o in g.options" :key="o.id" :value="o.id">
              {{ o.name }} <span v-if="+o.extra_price">+¥{{ o.extra_price }}</span>
            </el-radio>
          </el-radio-group>
          <el-checkbox-group v-else v-model="multiOptions[g.id]">
            <el-checkbox v-for="o in g.options" :key="o.id" :value="o.id">
              {{ o.name }} <span v-if="+o.extra_price">+¥{{ o.extra_price }}</span>
            </el-checkbox>
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
import { setCustomerToken } from '@/utils/customerAuth'
import { mediaUrl, thumbSizePx } from '@/utils/media'
import {
  applyPreferencesToProduct,
  sortPrefsForApply,
  buildOptionLabels,
  calcUnitPrice,
  checkCategoryRequire,
  collectOptionIds,
  mergeCartItem,
} from '@/utils/cart'

const route = useRoute()
const table = ref<any>(null)
const categories = ref<any[]>([])
const products = ref<any[]>([])
const showProduct = ref(false)
const currentProduct = ref<any>(null)
const selectedOptions = reactive<Record<number, number>>({})
const multiOptions = reactive<Record<number, number[]>>({})
const cart = ref<any[]>(JSON.parse(localStorage.getItem('snh_cart') || localStorage.getItem('cart') || '[]'))
const thumbPx = ref(thumbSizePx())
const heroHeight = ref(Math.min(typeof window !== 'undefined' ? window.innerWidth : 375, 480))

const showMemberPrompt = ref(false)
const nickname = ref('')
const enableFace = ref(false)
const registering = ref(false)
const customerId = ref<number | null>(null)
const customerToken = ref<string | null>(null)
const memberText = ref('注册会员后可查看历史订单、口味偏好，并支持一键复购。')
const faceText = ref('开启人脸迎宾后，到店可自动欢迎并推荐您常点口味。仅在您主动授权后生效，可随时关闭。当前为 basic anti-spoof（非真活体）。')

const cartCount = computed(() => cart.value.reduce((s, i) => s + i.quantity, 0))
const cartTotal = computed(() => cart.value.reduce((s, i) => s + parseFloat(i.subtotal), 0).toFixed(2))

function productsByCategory(catId: number) {
  return products.value.filter(p => p.category_id === catId)
}

function decorate(p: any) {
  return { ...p, description: p.description || '', _imageUrl: mediaUrl(p.main_image) }
}

const prefHint = ref('')

async function openProduct(p: any) {
  const res = await api.get(`/products/${p.id}`)
  currentProduct.value = decorate(res.data)
  prefHint.value = ''
  Object.keys(selectedOptions).forEach((k) => delete selectedOptions[Number(k)])
  Object.keys(multiOptions).forEach((k) => delete multiOptions[Number(k)])
  for (const g of currentProduct.value.option_groups || []) {
    if (g.selection_type === 'SINGLE' && g.options?.length) {
      const normal = g.options.find((o: any) => o.code === 'NORMAL') || g.options[0]
      selectedOptions[g.id] = normal.id
    }
    if (g.selection_type === 'MULTIPLE') {
      multiOptions[g.id] = []
    }
  }
  if (customerId.value) {
    try {
      const sid = table.value?.store_id
      const prefRes = await api.get(`/customers/${customerId.value}/preferences`, {
        params: sid ? { store_id: sid } : {},
      })
      const raw = prefRes.data?.all?.length ? prefRes.data.all : (prefRes.data?.preferences || [])
      const prefs = sortPrefsForApply(raw)
      const applied = applyPreferencesToProduct(
        currentProduct.value,
        prefs,
        selectedOptions,
        multiOptions,
      )
      if (applied > 0) prefHint.value = '已按您的智能口味爱好预选规格，可随时修改'
    } catch {
      /* ignore */
    }
  }
  showProduct.value = true
}

function addToCart() {
  const p = currentProduct.value
  if (!p) return
  const optionIds = collectOptionIds(p, selectedOptions, multiOptions)
  for (const g of p.option_groups || []) {
    if (!g.is_required) continue
    const has =
      (g.selection_type === 'SINGLE' && selectedOptions[g.id]) ||
      (g.selection_type === 'MULTIPLE' && (multiOptions[g.id] || []).length)
    if (!has) {
      ElMessage.warning(`请选择：${g.name}`)
      return
    }
  }
  const requireCheck = checkCategoryRequire(p, cart.value, products.value, categories.value)
  if (!requireCheck.ok) {
    ElMessage.warning(requireCheck.message)
    return
  }
  const stock = Number(p.stock ?? 999)
  if (stock < 999 && stock < 1) {
    ElMessage.error('库存不足')
    return
  }
  const optionLabels = buildOptionLabels(p, selectedOptions, multiOptions)
  const unitPrice = calcUnitPrice(p, selectedOptions, multiOptions)
  cart.value = mergeCartItem(cart.value, {
    product_id: p.id,
    name: p.name,
    quantity: 1,
    option_ids: optionIds,
    option_labels: optionLabels,
    option_text: optionLabels.join('、'),
    unit_price: unitPrice,
    price: p.price,
    subtotal: unitPrice.toFixed(2),
    imageUrl: p._imageUrl || '',
    box_price: p.box_price || '0',
    no_member_discount: !!p.no_member_discount,
    category_id: p.category_id,
    require_category_ids: p.require_category_ids || [],
    require_product_ids: p.require_product_ids || [],
  })
  localStorage.setItem('snh_cart', JSON.stringify(cart.value))
  localStorage.setItem('cart', JSON.stringify(cart.value))
  const bindSid = table.value?.store_id
  if (bindSid != null && Number(bindSid) > 0) {
    localStorage.setItem('snh_cart_store_id', String(Number(bindSid)))
  }
  showProduct.value = false
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
  thumbPx.value = thumbSizePx()
  heroHeight.value = Math.min(window.innerWidth, 480)

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
      if (table.value) {
        localStorage.setItem('snh_table', JSON.stringify(table.value))
      }
      if (entry.data?.prompts) {
        memberText.value = entry.data.prompts.member_consent_text
        faceText.value = entry.data.prompts.face_consent_text
      }
    } catch {
      table.value = (await api.get(`/tables/resolve/${token}`)).data
      if (table.value) {
        localStorage.setItem('snh_table', JSON.stringify(table.value))
      }
    }
  }

  // 生产 build 禁止伪造 login code（仅开发/预览可用本地假 code）
  if (import.meta.env.PROD) {
    ElMessage.warning('生产环境请使用微信/支付宝小程序登录，H5 不支持伪造 code')
  } else {
    const channelCode = channel === 'alipay' ? `h5_ali_${Date.now()}` : `h5_wx_${Date.now()}`
    const loginPath = channel === 'alipay' ? '/miniprogram/alipay/login' : '/miniprogram/wechat/login'
    const loginBody = channel === 'alipay'
      ? { auth_code: channelCode, table_token: token || localStorage.getItem('snh_table_token') }
      : { code: channelCode, table_token: token || localStorage.getItem('snh_table_token') }
    try {
      const res = await api.post(loginPath, loginBody, { headers: { 'X-Client': 'customer' } })
      customerId.value = res.data.customer_id
      customerToken.value = res.data.customer_token
      setCustomerToken(customerToken.value || '', customerId.value ?? undefined)
    } catch (e: any) {
      ElMessage.warning(e.message || '顾客登录失败（开发环境假 code；生产需真实小程序 code）')
    }
  }

  if (!localStorage.getItem('member_prompt_done')) {
    showMemberPrompt.value = true
  }

  const sid = table.value?.store_id
  if (!sid) {
    ElMessage.warning('请先扫桌码进入点餐')
    return
  }
  // 跨店购物车隔离：门店不一致则清空，防止串单
  const boundCartStore = Number(localStorage.getItem('snh_cart_store_id') || 0)
  if (boundCartStore > 0 && boundCartStore !== Number(sid)) {
    cart.value = []
    localStorage.removeItem('snh_cart')
    localStorage.removeItem('cart')
  }
  localStorage.setItem('snh_cart_store_id', String(Number(sid)))
  categories.value = (
    await api.get('/categories', {
      params: { store_id: sid },
    })
  ).data
  const allItems: any[] = []
  let page = 1
  let totalPages = 1
  do {
    const res: any = await api.get('/products', {
      params: {
        page,
        page_size: 200,
        store_id: sid,
      },
    })
    const data = res?.data || {}
    allItems.push(...(data.items || []))
    totalPages = Math.max(1, Number(data.total_pages || 1))
    page += 1
  } while (page <= totalPages && page <= 50)
  products.value = allItems.map(decorate)
})
</script>

<style scoped>
.mobile-order { max-width: 480px; margin: 0 auto; padding-bottom: 60px; position: relative; }
header { background: linear-gradient(135deg, #ff6b35, #f7b733); color: #fff; padding: 16px; text-align: center; }
.header-logo {
  display: block;
  width: 72px;
  height: 72px;
  object-fit: contain;
  margin: 0 auto 8px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.92);
}
header h1 { font-size: 18px; font-weight: 600; }
.section { padding: 12px; }
.product {
  display: flex; gap: 12px; align-items: stretch;
  padding: 12px; border-bottom: 1px solid #eee; cursor: pointer;
}
.thumb-wrap { flex-shrink: 0; border-radius: 8px; overflow: hidden; background: #f3f3f3; }
.thumb { width: 100%; height: 100%; object-fit: cover; display: block; }
.thumb.placeholder {
  width: 100%; height: 100%; display: flex; align-items: center; justify-content: center;
  background: linear-gradient(145deg, #ffe8df, #f5f5f5); color: #ff6b35; font-weight: 700; font-size: 22px;
}
.info { flex: 1; min-width: 0; }
.info p { color: #999; font-size: 13px; margin: 4px 0 0; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.price { color: #ff6b35; font-weight: bold; font-size: 18px; margin-top: 8px; }
.cart-bar {
  position: fixed; bottom: 0; left: 0; right: 0; max-width: 480px; margin: 0 auto;
  background: #333; color: #fff; padding: 16px; text-align: center; cursor: pointer;
}
.drawer-body { padding-bottom: 24px; }
.hero { width: 100%; border-radius: 12px; overflow: hidden; background: #eee; margin-bottom: 12px; }
.hero-img { width: 100%; height: 100%; object-fit: cover; display: block; }
.hero-img.placeholder {
  display: flex; align-items: center; justify-content: center;
  background: linear-gradient(160deg, #ffe8df, #f0f0f0); color: #ff6b35; font-size: 48px; font-weight: 700;
}
.desc { color: #888; font-size: 13px; margin: 4px 0 12px; }
.pref-hint {
  font-size: 13px; color: #ff6b35; background: #fff3ee;
  padding: 8px 12px; border-radius: 8px; margin-bottom: 8px;
}
.option-group { margin: 12px 0; }
.prompt-mask {
  position: fixed; inset: 0; background: rgba(0,0,0,.45); z-index: 100;
  display: flex; align-items: center; justify-content: center; padding: 16px;
}
.prompt-card {
  background: #fff; border-radius: 16px; padding: 20px; width: 100%; max-width: 400px;
}
.prompt-card h2 { font-size: 18px; margin-bottom: 8px; }
.prompt-card .desc { color: #666; font-size: 14px; line-height: 1.5; margin-bottom: 12px; }
.face-tip { color: #999; font-size: 12px; margin-top: 8px; line-height: 1.5; }
</style>
