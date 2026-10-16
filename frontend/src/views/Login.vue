<template>
  <div class="login-page">
    <!-- 背景层 -->
    <div class="bg-grid"></div>
    <div class="bg-glow glow-1"></div>
    <div class="bg-glow glow-2"></div>
    <div class="scanline"></div>
    <div class="noise"></div>

    <!-- 卡片包一层，专用于入场动画，避免后续状态切换重复触发 -->
    <div class="login-card-wrap">
      <el-card
        class="login-card"
        :class="{
          'is-success': state === 'success',
          'is-error': state === 'error'
        }"
        shadow="never"
      >
        <!-- 四角装饰 -->
        <span class="corner tl"></span>
        <span class="corner tr"></span>
        <span class="corner bl"></span>
        <span class="corner br"></span>

        <div class="logo-wrap">
          <div class="logo-ring outer"></div>
          <div class="logo-ring inner"></div>
          <img src="/logo.png?v=202609080938" alt="肠旺面" class="login-logo" />
        </div>

        <h2 class="title">肠旺面经营管理系统</h2>
        <p class="subtitle">SPICY NOODLE · MANAGEMENT SYSTEM</p>

        <el-form @submit.prevent="handleLogin">
          <el-form-item>
            <el-input
              v-model="username"
              placeholder="请输入用户名"
              size="large"
              :disabled="state !== 'idle'"
            />
          </el-form-item>
          <el-form-item>
            <el-input
              v-model="password"
              type="password"
              placeholder="请输入密码"
              size="large"
              show-password
              :disabled="state !== 'idle'"
            />
          </el-form-item>

          <button
            type="submit"
            class="login-btn"
            :class="{
              'is-loading': state === 'loading',
              'is-success': state === 'success',
              'is-error': state === 'error'
            }"
            :disabled="state !== 'idle'"
            @click="spawnRipple"
          >
            <span class="btn-label">{{ btnLabel }}</span>
            <!-- 点击涟漪 -->
            <span
              v-for="r in ripples"
              :key="r.id"
              class="ripple"
              :style="{ left: r.x + 'px', top: r.y + 'px' }"
            ></span>
          </button>
        </el-form>
      </el-card>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const auth = useAuthStore()

// 不预填真实凭据，避免默认弱口令泄露
const username = ref('')
const password = ref('')

// 登录流程状态机：idle → loading → success / error → idle
type LoginState = 'idle' | 'loading' | 'success' | 'error'
const state = ref<LoginState>('idle')

// 按钮文案随状态变化
const btnLabel = computed(() => {
  switch (state.value) {
    case 'loading': return '验 证 中'
    case 'success': return '登 录 成 功'
    case 'error':   return '登 录 失 败'
    default:        return '登 录'
  }
})

/* ---------- 点击涟漪 ---------- */
interface Ripple { id: number; x: number; y: number }
const ripples = ref<Ripple[]>([])
let rippleSeq = 0

function spawnRipple(e: MouseEvent) {
  if (state.value !== 'idle') return
  const btn = e.currentTarget as HTMLElement
  const rect = btn.getBoundingClientRect()
  const id = ++rippleSeq
  ripples.value.push({
    id,
    x: e.clientX - rect.left,
    y: e.clientY - rect.top
  })
  // 动画结束后移除，避免 DOM 无限增长
  setTimeout(() => {
    ripples.value = ripples.value.filter(r => r.id !== id)
  }, 800)
}

/* ---------- 登录主流程 ---------- */
async function handleLogin() {
  // 防抖：仅 idle 状态允许发起
  if (state.value !== 'idle') return

  state.value = 'loading'

  try {
    await auth.login(username.value, password.value)

    // 成功：先亮一下再跳转
    state.value = 'success'
    await new Promise(r => setTimeout(r, 850))

    if (auth.user?.role === 'CASHIER') {
      router.push('/pos-terminal')
    } else if (auth.user?.role === 'KITCHEN') {
      router.push('/kds')
    } else {
      router.push('/dashboard')
    }
  } catch (e: any) {
    state.value = 'error'
    ElMessage.error(e.message || '登录失败')
    // 让错误状态停留一会儿，再回到可点击
    await new Promise(r => setTimeout(r, 700))
    state.value = 'idle'
  }
}
</script>

<style scoped>
/* ============ 页面容器：中等深度藏蓝 ============ */
.login-page {
  position: relative;
  display: flex;
  justify-content: center;
  align-items: center;
  height: 100vh;
  overflow: hidden;
  background:
    radial-gradient(circle at 50% -10%, #244a86 0%, #16305c 45%, #0c1c39 100%);
}

/* ============ 透视网格 ============ */
.bg-grid {
  position: absolute;
  inset: -60% -20% -20%;
  background-image:
    linear-gradient(rgba(120, 195, 255, 0.14) 1px, transparent 1px),
    linear-gradient(90deg, rgba(120, 195, 255, 0.14) 1px, transparent 1px);
  background-size: 46px 46px;
  transform: perspective(520px) rotateX(62deg);
  transform-origin: center bottom;
  animation: grid-move 6s linear infinite;
  -webkit-mask-image: radial-gradient(ellipse at 50% 40%, #000 0%, transparent 72%);
  mask-image: radial-gradient(ellipse at 50% 40%, #000 0%, transparent 72%);
  pointer-events: none;
}
@keyframes grid-move {
  to { background-position: 0 46px, 46px 0; }
}

/* ============ 霓虹光斑 ============ */
.bg-glow {
  position: absolute;
  border-radius: 50%;
  filter: blur(95px);
  pointer-events: none;
}
.glow-1 {
  width: 420px; height: 420px;
  top: -140px; left: -120px;
  background: #4a8fe7;
  opacity: 0.5;
  animation: float-a 12s ease-in-out infinite;
}
.glow-2 {
  width: 480px; height: 480px;
  bottom: -180px; right: -150px;
  background: #62d6f0;
  opacity: 0.42;
  animation: float-b 15s ease-in-out infinite;
}
@keyframes float-a {
  0%, 100% { transform: translate(0, 0); }
  50%      { transform: translate(50px, 40px); }
}
@keyframes float-b {
  0%, 100% { transform: translate(0, 0); }
  50%      { transform: translate(-60px, -50px); }
}

/* ============ 扫描线 ============ */
.scanline {
  position: absolute;
  left: 0; right: 0;
  height: 2px;
  background: linear-gradient(90deg, transparent, rgba(110, 205, 255, 0.7), transparent);
  box-shadow: 0 0 20px rgba(110, 205, 255, 0.7);
  animation: scan 7s linear infinite;
  pointer-events: none;
}
@keyframes scan {
  0%   { top: -2%; opacity: 0; }
  10%  { opacity: 1; }
  90%  { opacity: 1; }
  100% { top: 102%; opacity: 0; }
}

/* ============ 噪点（细微颗粒质感） ============ */
.noise {
  position: absolute;
  inset: 0;
  opacity: 0.03;
  pointer-events: none;
  background-image: radial-gradient(#cfe8ff 1px, transparent 1px);
  background-size: 3px 3px;
}

/* ============ 卡片入场包装 ============ */
.login-card-wrap {
  animation: card-in 0.6s cubic-bezier(0.2, 0.8, 0.3, 1) both;
}
@keyframes card-in {
  from { opacity: 0; transform: translateY(22px) scale(0.97); }
  to   { opacity: 1; transform: translateY(0) scale(1); }
}

/* ============ 卡片：半透明深蓝玻璃 ============ */
.login-card {
  position: relative;
  width: 400px;
  padding: 38px 34px 30px;
  border: 1px solid rgba(120, 200, 255, 0.4);
  border-radius: 16px;
  background: rgba(26, 50, 92, 0.62);
  backdrop-filter: blur(16px) saturate(140%);
  -webkit-backdrop-filter: blur(16px) saturate(140%);
  box-shadow:
    0 0 32px rgba(80, 165, 245, 0.28),
    0 20px 55px rgba(4, 14, 32, 0.5),
    inset 0 0 44px rgba(120, 190, 255, 0.06);
  transition: border-color 0.35s ease, box-shadow 0.35s ease;
}
:deep(.el-card__body) {
  padding: 0;
  overflow: visible;
}

/* 顶部流动高光线 */
.login-card::before {
  content: '';
  position: absolute;
  top: -1px; left: 12%;
  width: 76%; height: 1px;
  background: linear-gradient(90deg, transparent, #7fc8ff, transparent);
  filter: drop-shadow(0 0 6px rgba(127, 200, 255, 0.85));
  animation: beam 3.5s ease-in-out infinite;
}
@keyframes beam {
  0%, 100% { opacity: 0.35; transform: scaleX(0.6); }
  50%      { opacity: 1;    transform: scaleX(1); }
}

/* ============ 四角装饰 ============ */
.corner {
  position: absolute;
  width: 16px; height: 16px;
  border: 2px solid #7fc8ff;
  filter: drop-shadow(0 0 5px rgba(127, 200, 255, 0.8));
  pointer-events: none;
  transition: border-color 0.35s ease;
}
.corner.tl { top: -1px; left: -1px;  border-right: 0; border-bottom: 0; border-radius: 16px 0 0 0; }
.corner.tr { top: -1px; right: -1px; border-left: 0;  border-bottom: 0; border-radius: 0 16px 0 0; }
.corner.bl { bottom: -1px; left: -1px;  border-right: 0; border-top: 0; border-radius: 0 0 0 16px; }
.corner.br { bottom: -1px; right: -1px; border-left: 0;  border-top: 0; border-radius: 0 0 16px 0; }

/* ============ Logo ============ */
.logo-wrap {
  position: relative;
  width: 108px;
  height: 108px;
  margin: 0 auto 18px;
}
.login-logo {
  position: relative;
  z-index: 2;
  display: block;
  width: 100%;
  height: 100%;
  padding: 8px;
  box-sizing: border-box;
  object-fit: contain;
  border-radius: 50%;
  background: #fff;
  box-shadow: 0 0 24px rgba(120, 200, 255, 0.6), 0 0 55px rgba(90, 170, 250, 0.35);
}
.logo-ring {
  position: absolute;
  border-radius: 50%;
  pointer-events: none;
}
.logo-ring.outer {
  inset: -12px;
  border: 1px dashed rgba(127, 200, 255, 0.55);
  animation: spin 14s linear infinite;
}
.logo-ring.inner {
  inset: -4px;
  border: 1px solid rgba(127, 200, 255, 0.32);
  border-top-color: #7fc8ff;
  animation: spin 3.5s linear infinite reverse;
}
@keyframes spin {
  to { transform: rotate(360deg); }
}

/* ============ 标题 ============ */
.title {
  margin: 0 0 8px;
  text-align: center;
  font-size: 20px;
  font-weight: 700;
  letter-spacing: 2px;
  color: #e8f4ff;
  text-shadow: 0 0 14px rgba(127, 200, 255, 0.65);
}
.subtitle {
  margin: 0 0 28px;
  text-align: center;
  font-size: 10px;
  letter-spacing: 3px;
  color: rgba(170, 215, 255, 0.6);
  user-select: none;
}

/* ============ 输入框 ============ */
:deep(.el-form-item) {
  margin-bottom: 20px;
}
:deep(.el-input__wrapper) {
  padding: 4px 14px;
  border-radius: 10px;
  background: rgba(16, 34, 66, 0.72);
  box-shadow: inset 0 0 0 1px rgba(127, 200, 255, 0.32);
  transition: box-shadow 0.25s ease, background 0.25s ease;
}
:deep(.el-input__wrapper:hover) {
  box-shadow: inset 0 0 0 1px rgba(127, 200, 255, 0.55);
}
:deep(.el-input__wrapper.is-focus) {
  background: rgba(18, 40, 76, 0.92);
  box-shadow:
    inset 0 0 0 1px #7fc8ff,
    0 0 14px rgba(127, 200, 255, 0.42);
}
:deep(.el-input__inner) {
  height: 40px;
  color: #e2f2ff;
  letter-spacing: 1px;
  caret-color: #7fc8ff;
}
:deep(.el-input__inner::placeholder) {
  color: rgba(160, 200, 240, 0.5);
}
/* 自动填充时不要变成刺眼的浅黄底 */
:deep(.el-input__inner:-webkit-autofill) {
  -webkit-text-fill-color: #e2f2ff;
  transition: background-color 9999s ease-in-out 0s;
}
:deep(.el-input.is-disabled .el-input__wrapper) {
  background: rgba(12, 26, 52, 0.55);
  box-shadow: inset 0 0 0 1px rgba(127, 200, 255, 0.15);
}
:deep(.el-input.is-disabled .el-input__inner) {
  color: rgba(180, 215, 245, 0.4);
}

/* =================================================== */
/*                    登录按钮                          */
/* =================================================== */
.login-btn {
  position: relative;
  display: block;
  width: 100%;
  height: 46px;
  margin-top: 4px;
  padding: 0;
  border: none;
  border-radius: 10px;
  font-family: inherit;
  font-size: 15px;
  font-weight: 700;
  letter-spacing: 4px;
  text-indent: 4px;      /* 补偿 letter-spacing 的尾部空白 */
  text-align: center;
  line-height: 46px;
  color: #08203c;
  background: linear-gradient(100deg, #7fd4ff 0%, #4f9bf5 50%, #7fd4ff 100%);
  background-size: 220% 100%;
  background-position: 0 0;
  box-shadow: 0 0 22px rgba(100, 185, 255, 0.5), inset 0 0 0 1px rgba(255, 255, 255, 0.3);
  cursor: pointer;
  overflow: hidden;
  transition:
    background-position 0.6s ease,
    box-shadow 0.3s ease,
    transform 0.15s ease,
    background 0.35s ease,
    color 0.35s ease;
}
.login-btn:hover:not(:disabled) {
  background-position: 100% 0;
  box-shadow: 0 0 32px rgba(100, 185, 255, 0.8), inset 0 0 0 1px rgba(255, 255, 255, 0.5);
  transform: translateY(-1px);
}
.login-btn:active:not(:disabled) {
  transform: translateY(0);
  box-shadow: 0 0 16px rgba(100, 185, 255, 0.6);
}
.login-btn:disabled {
  cursor: not-allowed;
}

.btn-label {
  position: relative;
  z-index: 2;
}

/* 掠过光带（空闲 & 加载共用） */
.login-btn::after {
  content: '';
  position: absolute;
  top: 0;
  left: -70%;
  width: 45%;
  height: 100%;
  background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.7), transparent);
  transform: skewX(-22deg);
  animation: sweep 3.2s ease-in-out infinite;
  pointer-events: none;
}
@keyframes sweep {
  0%        { left: -70%; }
  55%, 100% { left: 140%; }
}

/* --- 加载中：光带加速 + 按钮呼吸 --- */
.login-btn.is-loading {
  background: linear-gradient(100deg, #5fb8f0 0%, #3b7fe0 50%, #5fb8f0 100%);
  background-size: 220% 100%;
  animation: btn-breath 1.2s ease-in-out infinite;
}
.login-btn.is-loading::after {
  animation-duration: 1s;
}
@keyframes btn-breath {
  0%, 100% { box-shadow: 0 0 18px rgba(100, 185, 255, 0.5), inset 0 0 0 1px rgba(255, 255, 255, 0.3); }
  50%      { box-shadow: 0 0 34px rgba(100, 185, 255, 0.95), inset 0 0 0 1px rgba(255, 255, 255, 0.55); }
}

/* --- 成功：整块转绿 + 光带消失 --- */
.login-btn.is-success {
  background: linear-gradient(100deg, #6ee7a7 0%, #34c97b 50%, #6ee7a7 100%);
  background-size: 220% 100%;
  background-position: 100% 0;
  color: #043d23;
  box-shadow:
    0 0 34px rgba(74, 222, 128, 0.8),
    inset 0 0 0 1px rgba(255, 255, 255, 0.55);
}
.login-btn.is-success::after {
  animation: none;
  opacity: 0;
}

/* --- 失败：转红 --- */
.login-btn.is-error {
  background: linear-gradient(100deg, #ffb3b3 0%, #ff7a7a 50%, #ffb3b3 100%);
  color: #5a0e0e;
  box-shadow:
    0 0 24px rgba(255, 120, 120, 0.6),
    inset 0 0 0 1px rgba(255, 255, 255, 0.4);
}
.login-btn.is-error::after {
  animation: none;
  opacity: 0;
}

/* --- 点击涟漪 --- */
.ripple {
  position: absolute;
  width: 20px;
  height: 20px;
  margin-left: -10px;
  margin-top: -10px;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(255, 255, 255, 0.95) 0%, rgba(255, 255, 255, 0) 70%);
  pointer-events: none;
  animation: ripple-out 0.8s cubic-bezier(0.2, 0.6, 0.4, 1) forwards;
  z-index: 1;
}
@keyframes ripple-out {
  0%   { transform: scale(0);  opacity: 1; }
  100% { transform: scale(28); opacity: 0; }
}

/* =================================================== */
/*              卡片状态：成功 / 失败                    */
/* =================================================== */

/* 成功：整体染绿 + 一圈扩散光环 */
.login-card.is-success {
  border-color: rgba(74, 222, 128, 0.7);
  box-shadow:
    0 0 50px rgba(74, 222, 128, 0.6),
    0 20px 55px rgba(4, 14, 32, 0.5),
    inset 0 0 55px rgba(74, 222, 128, 0.14);
}
.login-card.is-success .corner {
  border-color: #6ee7a7;
  filter: drop-shadow(0 0 6px rgba(110, 231, 167, 0.9));
}
.login-card.is-success::before {
  background: linear-gradient(90deg, transparent, #6ee7a7, transparent);
  filter: drop-shadow(0 0 8px rgba(110, 231, 167, 0.9));
}
.login-card.is-success::after {
  content: '';
  position: absolute;
  inset: -1px;
  border-radius: 16px;
  box-shadow: 0 0 0 0 rgba(74, 222, 128, 0.7);
  animation: success-ring 0.9s ease-out;
  pointer-events: none;
}
@keyframes success-ring {
  0%   { box-shadow: 0 0 0 0 rgba(74, 222, 128, 0.7); }
  100% { box-shadow: 0 0 0 60px rgba(74, 222, 128, 0); }
}

/* 失败：左右震动 + 边框转红 */
.login-card.is-error {
  border-color: rgba(255, 100, 100, 0.75);
  box-shadow:
    0 0 40px rgba(255, 100, 100, 0.45),
    0 20px 55px rgba(4, 14, 32, 0.5),
    inset 0 0 40px rgba(255, 100, 100, 0.1);
  animation: shake 0.5s cubic-bezier(.36, .07, .19, .97);
}
.login-card.is-error .corner {
  border-color: #ff9a9a;
  filter: drop-shadow(0 0 6px rgba(255, 154, 154, 0.85));
}
@keyframes shake {
  10%, 90% { transform: translateX(-2px); }
  20%, 80% { transform: translateX(4px); }
  30%, 50%, 70% { transform: translateX(-6px); }
  40%, 60% { transform: translateX(6px); }
}

/* ============ 小屏适配 ============ */
@media (max-width: 480px) {
  .login-card {
    width: 90vw;
    padding: 30px 22px 24px;
  }
  .logo-wrap { width: 88px; height: 88px; }
  .title { font-size: 17px; letter-spacing: 1px; }
}

/* 尊重用户的「减少动态效果」偏好 */
@media (prefers-reduced-motion: reduce) {
  .bg-grid,
  .scanline,
  .logo-ring,
  .login-btn::after,
  .login-card::before,
  .bg-glow,
  .login-btn.is-loading,
  .login-card.is-error,
  .login-card.is-success::after,
  .login-card-wrap {
    animation: none;
  }
}
</style>