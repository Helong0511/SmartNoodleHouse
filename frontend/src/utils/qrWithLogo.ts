import QRCode from 'qrcode'
import { BRAND_LOGO_URL } from './brand'

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image()
    img.crossOrigin = 'anonymous'
    img.onload = () => resolve(img)
    img.onerror = () => reject(new Error(`logo 加载失败: ${src}`))
    img.src = src
  })
}

function roundRect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  r: number,
) {
  const radius = Math.min(r, w / 2, h / 2)
  ctx.beginPath()
  ctx.moveTo(x + radius, y)
  ctx.arcTo(x + w, y, x + w, y + h, radius)
  ctx.arcTo(x + w, y + h, x, y + h, radius)
  ctx.arcTo(x, y + h, x, y, radius)
  ctx.arcTo(x, y, x + w, y, radius)
  ctx.closePath()
}

/** object-fit: cover — 等比放大后居中裁切填满 box */
function drawImageCover(
  ctx: CanvasRenderingContext2D,
  img: HTMLImageElement,
  x: number,
  y: number,
  box: number,
) {
  const lw = img.naturalWidth || img.width
  const lh = img.naturalHeight || img.height
  const scale = Math.max(box / lw, box / lh)
  const dw = lw * scale
  const dh = lh * scale
  const dx = x + (box - dw) / 2
  const dy = y + (box - dh) / 2
  ctx.drawImage(img, dx, dy, dw, dh)
}

/**
 * 生成带中心 Logo 的二维码 Data URL（高纠错 H）。
 * Logo 以 cover 填充圆角方区，适配正方形品牌图。
 */
export async function qrDataUrlWithLogo(
  content: string,
  options?: {
    size?: number
    logoUrl?: string
    /** Logo 区域相对二维码边长比例，建议 0.20～0.24 */
    logoRatio?: number
  },
): Promise<string> {
  const text = String(content || '').trim() || ' '
  const size = options?.size ?? 440
  const logoUrl = options?.logoUrl || BRAND_LOGO_URL
  const logoRatio = options?.logoRatio ?? 0.22

  const canvas = document.createElement('canvas')
  await QRCode.toCanvas(canvas, text, {
    errorCorrectionLevel: 'H',
    margin: 2,
    width: size,
    color: { dark: '#000000', light: '#ffffff' },
  })

  const ctx = canvas.getContext('2d')
  if (!ctx) return canvas.toDataURL('image/png')

  try {
    const logo = await loadImage(logoUrl)
    const box = Math.floor(size * logoRatio)
    const x = (size - box) / 2
    const y = (size - box) / 2
    const pad = Math.max(6, Math.floor(box * 0.12))
    const radius = Math.floor(box * 0.18)

    ctx.fillStyle = '#ffffff'
    roundRect(ctx, x - pad, y - pad, box + pad * 2, box + pad * 2, radius + 2)
    ctx.fill()

    ctx.save()
    roundRect(ctx, x, y, box, box, radius)
    ctx.clip()
    drawImageCover(ctx, logo, x, y, box)
    ctx.restore()
  } catch {
    // Logo 失败时仍返回纯二维码，保证可扫
  }

  return canvas.toDataURL('image/png')
}
