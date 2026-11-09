<template>
  <div class="scan-page">
    <div v-if="loading" class="card">
      <div class="spinner" />
      <p>正在识别扫码环境…</p>
    </div>

    <div v-else-if="error" class="card error">
      <h2>无法打开桌台</h2>
      <p>{{ error }}</p>
    </div>

    <div v-else class="card">
      <img src="/logo.png?v=202609080938" alt="肠旺面" class="page-logo" />
      <p class="store" v-if="storeName">{{ storeName }}</p>
      <h1>{{ tableLabel }}</h1>
      <p class="sub">扫码点餐 · 桌号 {{ info?.table?.table_no }}</p>

      <p v-if="autoHint" class="hint">{{ autoHint }}</p>
      <p v-if="devHint" class="dev-hint">{{ devHint }}</p>

      <div class="actions">
        <button
          v-if="canJumpWechat"
          type="button"
          class="btn wechat"
          @click="openWechat"
        >微信点餐</button>
        <button
          v-else
          type="button"
          class="btn wechat"
          @click="guideWechatInApp"
        >如何打开微信小程序</button>

        <button
          v-if="info?.alipay_app_configured !== false && alipayHref"
          type="button"
          class="btn alipay"
          @click="openAlipay"
        >支付宝点餐</button>
        <button v-else type="button" class="btn alipay disabled" disabled>支付宝未配置 AppID</button>

        <button type="button" class="btn h5" @click="openH5">浏览器点餐</button>
        <button type="button" class="btn ghost" @click="copyPageUrl">复制本页链接（系统浏览器打开）</button>
        <button
          v-if="info?.wechat_scheme"
          type="button"
          class="btn ghost"
          @click="copyWechatScheme"
        >复制微信跳转 Scheme</button>
      </div>

      <p class="foot">
        本地开发：微信扫桌台码只会打开本 H5。要进开发版小程序，请用「系统浏览器」打开本页再点微信点餐，或用开发者工具「真机调试」二维码。
      </p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import api from '@/api'

type ScanInfo = {
  table: { table_no: string; table_name?: string; store_id: number }
  store?: { name?: string }
  wechat_scheme: string
  wechat_url_link?: string | null
  wechat_url_link_error?: string | null
  alipay_scheme: string
  h5_order_url: string
  mini_program_env_version?: string
  wechat_app_configured?: boolean
  alipay_app_configured?: boolean
}

const route = useRoute()
const loading = ref(true)
const error = ref('')
const info = ref<ScanInfo | null>(null)
const autoHint = ref('')
const devHint = ref('')
const tableLabel = ref('加载中…')
const storeName = ref('')

const alipayHref = computed(() => info.value?.alipay_scheme || '')

/** 微信内且无 URL Link 时，禁止跳 weixin://（会报「当前页面无法访问」） */
const canJumpWechat = computed(() => {
  if (!info.value) return false
  if (info.value.wechat_url_link) return true
  if (!isWechat()) return !!info.value.wechat_scheme
  return false
})

function ua() {
  return navigator.userAgent || ''
}

function isWechat() {
  return /MicroMessenger/i.test(ua())
}

function isAlipay() {
  return /AlipayClient|AliApp/i.test(ua())
}

const WECHAT_LINK_HOSTS = new Set([
  'wxaurl.cn',
  'weixin.qq.com',
  'mp.weixin.qq.com',
  'open.weixin.qq.com',
  'servicewechat.com',
])
const ALIPAY_LINK_HOSTS = new Set([
  'qr.alipay.com',
  'render.alipay.com',
  'openauth.alipay.com',
  'mclient.alipay.com',
  'mobilecodec.alipay.com',
])

function safeRedirectTarget(raw: string, kind: 'wechat' | 'alipay' | 'h5'): string | null {
  try {
    const target = new URL(String(raw || '').trim())
    if (kind === 'h5') {
      return target.protocol === 'https:' && target.hostname === window.location.hostname
        ? target.href
        : null
    }
    if (kind === 'wechat') {
      if (target.protocol === 'https:' && WECHAT_LINK_HOSTS.has(target.hostname.toLowerCase())) return target.href
      if (target.protocol === 'weixin:' && target.hostname.toLowerCase() === 'dl') return target.href
      return null
    }
    if (target.protocol === 'https:' && ALIPAY_LINK_HOSTS.has(target.hostname.toLowerCase())) return target.href
    if (target.protocol === 'alipays:' && target.hostname.toLowerCase() === 'platformapi') return target.href
    return null
  } catch {
    return null
  }
}

function tryOpen(url: string, kind: 'wechat' | 'alipay' | 'h5'): boolean {
  const safe = safeRedirectTarget(url, kind)
  if (!safe) {
    autoHint.value = '跳转地址无效或不在可信平台域名白名单内，请联系门店核对配置'
    return false
  }
  window.location.href = safe
  return true
}

async function copyText(text: string, okMsg: string) {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
    } else {
      const ta = document.createElement('textarea')
      ta.value = text
      ta.style.position = 'fixed'
      ta.style.left = '-9999px'
      document.body.appendChild(ta)
      ta.select()
      document.execCommand('copy')
      document.body.removeChild(ta)
    }
    autoHint.value = okMsg
  } catch {
    autoHint.value = `请手动复制：${text}`
  }
}

function openWechat() {
  if (!info.value) return
  const link = info.value.wechat_url_link
  const scheme = info.value.wechat_scheme
  if (link) {
    if (tryOpen(link, 'wechat')) return
  }
  // 仅系统浏览器可安全使用明文 Scheme 打开 develop
  if (!isWechat() && scheme) {
    if (tryOpen(scheme, 'wechat')) return
  }
  guideWechatInApp()
}

function guideWechatInApp() {
  const ver = info.value?.mini_program_env_version || 'develop'
  autoHint.value = '微信内无法直接打开开发版小程序'
  devHint.value = [
    `当前目标版本：${ver}`,
    info.value?.wechat_url_link_error || 'URL Link 未开通（errcode 85407）',
    '做法①：右上角「···」→ 在浏览器打开 → 再点「微信点餐」',
    '做法②：微信开发者工具 → 真机调试 → 扫工具生成的调试码（不是桌台码）',
    '做法③：公众平台开通明文 Scheme / URL Link 后刷新本页',
  ].join('\n')
}

function openAlipay() {
  if (!info.value?.alipay_scheme) return
  tryOpen(info.value.alipay_scheme, 'alipay')
}

function openH5() {
  if (!info.value) return
  const target = safeRedirectTarget(info.value.h5_order_url, 'h5')
  if (!target) {
    autoHint.value = 'H5 点餐地址必须为当前站点的 HTTPS 地址'
    return
  }
  const next = new URL(target)
  next.searchParams.set('channel', isAlipay() ? 'alipay' : 'wechat')
  window.location.href = next.href
}

function copyPageUrl() {
  copyText(window.location.href, '已复制本页链接，请粘贴到系统浏览器打开')
}

function copyWechatScheme() {
  if (!info.value?.wechat_scheme) return
  copyText(info.value.wechat_scheme, '已复制 Scheme，请在系统浏览器地址栏粘贴访问')
}

function buildDevHint(data: ScanInfo) {
  const parts: string[] = []
  const ver = data.mini_program_env_version || 'release'
  if (ver !== 'release') {
    parts.push(`当前跳转小程序版本：${ver}`)
  }
  if (data.wechat_url_link_error) {
    parts.push(data.wechat_url_link_error)
  } else if (isWechat() && !data.wechat_url_link && ver !== 'release') {
    parts.push('微信内打开开发版需 URL Link；请用系统浏览器打开本页')
  }
  if (isAlipay() && ver !== 'release') {
    parts.push('支付宝开发版需先扫 IDE 预览码并开启「联调设置」')
  }
  if (data.wechat_app_configured === false) {
    parts.push('请在 .env 配置 WECHAT_APP_ID')
  }
  if (data.alipay_app_configured === false) {
    parts.push('请在 .env 配置 ALIPAY_APP_ID')
  }
  return parts.join('；')
}

function autoRedirect() {
  if (!info.value) return
  devHint.value = buildDevHint(info.value)

  if (isWechat()) {
    if (info.value.wechat_url_link) {
      autoHint.value = '检测到微信，正在打开小程序…'
      openWechat()
    } else {
      autoHint.value = '检测到微信：开发版请用系统浏览器打开，或点下方按钮看步骤'
    }
    return
  }
  if (isAlipay()) {
    autoHint.value = '检测到支付宝，正在打开小程序…'
    openAlipay()
    return
  }
  autoHint.value = '请选择微信或支付宝打开小程序点餐'
}

onMounted(async () => {
  const token = String(route.query.token || '').trim()
  if (!token) {
    loading.value = false
    error.value = '缺少桌台参数，请重新扫描桌码'
    return
  }
  try {
    const res = (await api.get('/miniprogram/scan-landing', { params: { token } })) as {
      message?: string
      data?: ScanInfo | null
    }
    if (!res.data) {
      error.value = res.message || '桌台无效或已停用'
      return
    }
    info.value = res.data
    tableLabel.value = res.data.table.table_name || res.data.table.table_no || '堂食'
    storeName.value = res.data.store?.name || ''
    loading.value = false
    setTimeout(autoRedirect, 300)
  } catch (e: any) {
    error.value = e.message || '加载失败，请稍后重试'
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.scan-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px 16px;
  background: linear-gradient(160deg, #fff8f5 0%, #f5f7fa 55%);
}
.card {
  width: 100%;
  max-width: 400px;
  background: #fff;
  border-radius: 16px;
  padding: 28px 22px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.06);
  text-align: center;
}
.card.error h2 { color: #c45656; margin-bottom: 8px; }
.page-logo {
  display: block;
  width: 88px;
  height: 88px;
  object-fit: contain;
  margin: 0 auto 12px;
  border-radius: 12px;
  background: #fff;
}
.store { color: #888; font-size: 14px; margin-bottom: 4px; }
h1 { font-size: 28px; margin: 0 0 8px; color: #222; }
.sub { color: #666; font-size: 14px; margin-bottom: 20px; }
.hint { color: #ff6b35; font-size: 13px; margin-bottom: 12px; white-space: pre-wrap; }
.dev-hint {
  color: #666;
  font-size: 12px;
  line-height: 1.55;
  margin-bottom: 16px;
  text-align: left;
  white-space: pre-wrap;
  background: #f7f8fa;
  border-radius: 10px;
  padding: 12px;
}
.actions { display: flex; flex-direction: column; gap: 12px; }
.btn {
  border: none;
  border-radius: 10px;
  padding: 14px 16px;
  font-size: 16px;
  font-weight: 600;
  cursor: pointer;
  text-decoration: none;
  display: block;
}
.btn.wechat { background: #07c160; color: #fff; }
.btn.alipay { background: #1677ff; color: #fff; }
.btn.h5 { background: #f5f5f5; color: #333; }
.btn.ghost { background: #fff; color: #555; border: 1px solid #e5e5e5; font-weight: 500; font-size: 14px; }
.btn.disabled { opacity: 0.45; cursor: not-allowed; }
.foot { margin-top: 18px; color: #999; font-size: 12px; line-height: 1.5; text-align: left; }
.spinner {
  width: 36px;
  height: 36px;
  margin: 0 auto 12px;
  border: 3px solid #eee;
  border-top-color: #ff6b35;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
</style>
