/** 会员头像：本地临时路径需上传后才能持久展示 */

const auth = require('./auth')
const media = require('./media')

function isPersistableAvatarUrl(url) {
  const s = String(url || '').trim()
  if (!s) return false
  if (s.startsWith('/uploads/')) return true
  if (/^https?:\/\//i.test(s) && s.includes('/uploads/')) return true
  return false
}

function needsUpload(url) {
  const s = String(url || '').trim()
  if (!s) return false
  return !isPersistableAvatarUrl(s)
}

function resolveDisplayUrl(url) {
  const s = String(url || '').trim()
  if (!s) return ''
  if (needsUpload(s)) return s
  return media.resolveUrl(s)
}

function parseUploadBody(raw) {
  if (raw == null) return {}
  if (typeof raw === 'object') return raw
  try {
    return JSON.parse(String(raw))
  } catch (_) {
    return { message: String(raw) }
  }
}

/**
 * 将本地临时头像上传到服务端，返回持久 /uploads/avatars/... 路径
 */
async function uploadLocalAvatar(filePath) {
  const path = String(filePath || '').trim()
  if (!path) throw new Error('未选择头像文件')
  await auth.ensureLogin()
  const app = auth.getAppSafe()
  const base = (app && app.globalData && app.globalData.apiBase) || ''
  if (!base) throw new Error('未配置 apiBase')
  const token = (app && app.globalData && app.globalData.customerToken)
    || auth.readStorage('customer_token')
    || ''
  if (!token) throw new Error('请先登录后再上传头像')

  return new Promise((resolve, reject) => {
    my.uploadFile({
      url: `${base}/miniprogram/member/avatar`,
      filePath: path,
      fileName: 'file',
      name: 'file',
      header: {
        Authorization: `Bearer ${token}`
      },
      success(res) {
        const status = res.statusCode != null ? res.statusCode : res.status
        const body = parseUploadBody(res.data)
        const msg = body.message || body.detail || `上传失败(${status || '?'})`
        if ((status && status >= 400) || (body.code !== 0 && body.code !== undefined)) {
          reject(Object.assign({ message: String(msg) }, { code: body.code, status }))
          return
        }
        const data = body.data || body
        const avatarUrl = (data && data.avatar_url) || ''
        if (!avatarUrl) {
          reject({ message: '上传成功但未返回头像地址' })
          return
        }
        resolve(avatarUrl)
      },
      fail(err) {
        reject({
          message: (err && (err.errorMessage || err.errMsg || err.message)) || '头像上传失败'
        })
      }
    })
  })
}

/** 已是持久地址则原样返回；本地临时路径则上传 */
async function ensureRemoteAvatar(url) {
  const s = String(url || '').trim()
  if (!s) return ''
  if (!needsUpload(s)) {
    if (s.includes('/uploads/')) {
      const idx = s.indexOf('/uploads/')
      return s.slice(idx).split('?')[0]
    }
    return s
  }
  return uploadLocalAvatar(s)
}

module.exports = {
  isPersistableAvatarUrl,
  needsUpload,
  resolveDisplayUrl,
  uploadLocalAvatar,
  ensureRemoteAvatar
}
