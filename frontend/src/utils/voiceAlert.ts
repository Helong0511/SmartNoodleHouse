/** 厨房 KDS / 顾客叫号屏：中文语音播报 + 提示音（需用户手势解锁） */

const VOICE_SESSION_KEY = 'snh_voice_ok'

export const VOICE_SOUND_KEYS = {
  kds: 'snh_sound_kds',
  display: 'snh_sound_display',
} as const

let audioCtx: AudioContext | null = null
let unlocked = false
const speechQueue: string[] = []
let speaking = false
let voicesReady = false

function ensureVoices() {
  if (typeof window === 'undefined' || !('speechSynthesis' in window)) return
  const voices = window.speechSynthesis.getVoices()
  if (voices.length) {
    voicesReady = true
    return
  }
  window.speechSynthesis.addEventListener('voiceschanged', () => {
    if (window.speechSynthesis.getVoices().length) voicesReady = true
  }, { once: true })
}

export function isVoiceSupported() {
  return typeof window !== 'undefined' && 'speechSynthesis' in window
}

/** 仅反映本页内存态；不在此自动恢复 session，避免未手势解锁却误判为已开启 */
export function isVoiceUnlocked() {
  return unlocked
}

export function hasVoiceSessionHint() {
  try {
    return sessionStorage.getItem(VOICE_SESSION_KEY) === '1'
  } catch {
    return false
  }
}

/** 读取语音开关，默认开启 */
export function readVoiceSoundOn(key: string, defaultOn = true): boolean {
  if (typeof window === 'undefined') return defaultOn
  try {
    const raw = localStorage.getItem(key)
    if (raw === null) return defaultOn
    return raw === '1' || raw === 'true'
  } catch {
    return defaultOn
  }
}

export function writeVoiceSoundOn(key: string, on: boolean) {
  if (typeof window === 'undefined') return
  try {
    localStorage.setItem(key, on ? '1' : '0')
  } catch {
    /* ignore */
  }
}

export function stopAllVoice() {
  speechQueue.length = 0
  speaking = false
  if (typeof window === 'undefined' || !('speechSynthesis' in window)) return
  try {
    window.speechSynthesis.cancel()
  } catch {
    /* ignore */
  }
}

function markVoiceSession() {
  try {
    sessionStorage.setItem(VOICE_SESSION_KEY, '1')
  } catch {
    /* ignore */
  }
}

async function resumeAudio(): Promise<boolean> {
  if (typeof window === 'undefined') return false
  try {
    if (!audioCtx) audioCtx = new AudioContext()
    if (audioCtx.state === 'suspended') await audioCtx.resume()
    return audioCtx.state === 'running'
  } catch {
    return false
  }
}

function pickChineseVoice(): SpeechSynthesisVoice | null {
  if (typeof window === 'undefined') return null
  const voices = window.speechSynthesis.getVoices()
  return (
    voices.find((v) => v.lang === 'zh-CN')
    || voices.find((v) => v.lang.startsWith('zh'))
    || voices[0]
    || null
  )
}

/** 在用户点击回调中同步调用，成功率最高 */
export function speakNow(text: string): boolean {
  if (!isVoiceSupported() || !text.trim()) return false
  try {
    const voices = window.speechSynthesis.getVoices()
    if (!voices.length && !voicesReady) {
      window.speechSynthesis.addEventListener('voiceschanged', () => {
        speakNow(text)
      }, { once: true })
      window.speechSynthesis.getVoices()
      return true
    }

    window.speechSynthesis.cancel()
    speaking = false
    const utter = new SpeechSynthesisUtterance(text.trim())
    utter.lang = 'zh-CN'
    utter.rate = 1.05
    utter.pitch = 1
    const voice = pickChineseVoice()
    if (voice) utter.voice = voice
    utter.onend = () => {
      speaking = false
      processQueue()
    }
    utter.onerror = () => {
      speaking = false
      processQueue()
    }
    speaking = true
    window.speechSynthesis.speak(utter)
    return true
  } catch {
    speaking = false
    return false
  }
}

function processQueue() {
  if (speaking || !speechQueue.length || !unlocked) return
  if (!isVoiceSupported()) {
    speechQueue.length = 0
    return
  }
  const text = speechQueue.shift()!
  setTimeout(() => {
    if (!unlocked) return
    speakNow(text)
  }, 80)
}

export function flushVoiceQueue() {
  processQueue()
}

/** 浏览器要求至少一次用户点击后才能出声 */
export async function unlockVoice(confirmText?: string): Promise<boolean> {
  if (typeof window === 'undefined') return false

  ensureVoices()
  const audioOk = await resumeAudio()
  const speechOk = confirmText && isVoiceSupported() ? speakNow(confirmText) : false

  if (!audioOk && !speechOk) return false

  unlocked = true
  markVoiceSession()
  if (!speechOk) flushVoiceQueue()
  return true
}

/** 页面任意点击/按键时自动解锁（失败时不移除监听，直到成功） */
export function bindAutoVoiceUnlock(onUnlocked?: (ok: boolean) => void): () => void {
  if (typeof window === 'undefined') return () => {}

  const handler = async () => {
    if (unlocked) {
      cleanup()
      return
    }
    const ok = await unlockVoice('语音已开启')
    onUnlocked?.(ok)
    if (ok) cleanup()
  }

  const opts: AddEventListenerOptions = { capture: true, passive: true }
  const cleanup = () => {
    window.removeEventListener('pointerdown', handler, opts)
    window.removeEventListener('keydown', handler, opts)
  }

  window.addEventListener('pointerdown', handler, opts)
  window.addEventListener('keydown', handler, opts)
  return cleanup
}

export function beepTone(kind: 'new' | 'urge' | 'ready' | 'default' = 'default') {
  if (!unlocked || typeof window === 'undefined') return
  void resumeAudio().then((ok) => {
    if (!ok) return
    try {
      const ctx = audioCtx
      if (!ctx) return
      const osc = ctx.createOscillator()
      const gain = ctx.createGain()
      osc.connect(gain)
      gain.connect(ctx.destination)
      const freq = kind === 'urge' ? 880 : kind === 'ready' ? 784 : kind === 'new' ? 660 : 520
      osc.frequency.value = freq
      gain.gain.value = kind === 'ready' ? 0.14 : 0.11
      osc.start()
      setTimeout(() => osc.stop(), kind === 'urge' ? 320 : 200)
    } catch {
      /* ignore */
    }
  })
}

export function speakAlert(
  text: string,
  enabled = true,
  opts?: { beep?: boolean; beepKind?: 'new' | 'urge' | 'ready' | 'default' },
) {
  if (!enabled || !text.trim()) return false
  if (!unlocked) return false
  speechQueue.push(text.trim())
  if (opts?.beep !== false) beepTone(opts?.beepKind || 'default')
  processQueue()
  return true
}

export function kitchenNewOrderText(order?: Record<string, any>): string {
  if (!order) return '新订单，请查看厨房屏幕'
  if (order.is_delivery || order.channel === 'MEITUAN' || order.channel === 'ELEME') {
    const seq = order.delivery?.day_seq || String(order.order_no || '').slice(-4) || ''
    const platform = order.channel_label || (order.channel === 'ELEME' ? '饿了么' : '美团')
    return seq ? `新${platform}订单，序号${seq}，请制作` : `新${platform}订单，请制作`
  }
  const table = order.table_name || order.table_no || (order.table_id ? `${order.table_id}号桌` : '')
  if (table) return `新订单，${table}，请制作`
  if (order.need_pack || order.dining_type === 'TAKEAWAY') return '新打包订单，请制作'
  return '新订单，请查看厨房屏幕'
}

export function kitchenUrgeText() {
  return '外卖催单，请尽快制作'
}

export function pickupReadyText(label?: string | null): string {
  const l = String(label || '').trim()
  if (!l) return '餐品已备好，请取餐'
  if (l.includes('桌')) return `请${l}取餐`
  return `请取餐号${l}，可以取餐了`
}

export function announceKitchenNew(order?: Record<string, any>, enabled = true) {
  return speakAlert(kitchenNewOrderText(order), enabled, { beep: true, beepKind: 'new' })
}

export function announceKitchenUrge(enabled = true) {
  return speakAlert(kitchenUrgeText(), enabled, { beep: true, beepKind: 'urge' })
}

export function announcePickupReady(label?: string | null, enabled = true) {
  return speakAlert(pickupReadyText(label), enabled, { beep: true, beepKind: 'ready' })
}

ensureVoices()
